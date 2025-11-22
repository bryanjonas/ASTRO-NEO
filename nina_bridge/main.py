"""FastAPI bridge that fronts the real/mock NINA API."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException

from nina_bridge.config import settings as bridge_settings
from nina_bridge.models import (
    BridgeStatus,
    ConnectionRequest,
    DomeUpdate,
    ExposureRequest,
    FocuserMove,
    OverrideUpdate,
    ParkToggle,
    SequencePlanRequest,
    SequencePlanResponse,
    SequenceRequest,
    SlewCommand,
    TrackingMode,
    TrackingPayload,
)
from nina_bridge.state import STATE
from nina_bridge.templates import select_template

from app.db.session import get_session
from app.services.equipment import EquipmentProfile, get_active_equipment_profile
from app.services.weather import WeatherService, WeatherSummary
from app.services.presets import select_preset

logger = logging.getLogger("nina_bridge")

app = FastAPI(title="NINA Bridge", version="0.1.0")
API_PREFIX = "/api"


async def get_client() -> httpx.AsyncClient:
    """Yield a scoped HTTP client for outbound NINA calls."""
    async with httpx.AsyncClient(
        base_url=bridge_settings.nina_base_url,
        timeout=bridge_settings.http_timeout,
    ) as client:
        yield client


def _summary_to_dict(summary: WeatherSummary | None) -> dict[str, Any] | None:
    if not summary:
        return None
    return {
        "fetched_at": summary.fetched_at.isoformat(),
        "temperature_c": summary.temperature_c,
        "wind_speed_mps": summary.wind_speed_mps,
        "relative_humidity_pct": summary.relative_humidity_pct,
        "precipitation_probability_pct": summary.precipitation_probability_pct,
        "precipitation_mm": summary.precipitation_mm,
        "cloud_cover_pct": summary.cloud_cover_pct,
        "reasons": summary.reasons,
        "is_safe": summary.is_safe,
    }


def _current_weather(force_refresh: bool = False) -> WeatherSummary | None:
    with get_session() as session:
        service = WeatherService(session)
        return service.get_status(force_refresh=force_refresh)


def _profile_to_dict(profile: EquipmentProfile | None) -> dict[str, Any] | None:
    if not profile:
        return None
    return profile.model_dump()


def _load_equipment_profile() -> EquipmentProfile | None:
    try:
        return get_active_equipment_profile()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.warning("Unable to load equipment profile: %s", exc)
        return None


def _validate_camera_request(filter_name: str, binning: int) -> None:
    profile = _load_equipment_profile()
    if not profile:
        return
    camera = profile.camera
    allowed_filters = camera.filters or [filter_name]
    if camera.type.lower() == "osc":
        allowed_filters = camera.filters or ["C"]
    if filter_name not in allowed_filters:
        raise HTTPException(
            status_code=422,
            detail={"reason": "unsupported_filter", "allowed_filters": allowed_filters},
        )
    if binning > camera.max_binning:
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "binning_not_supported",
                "max_binning": camera.max_binning,
            },
        )


def _normalize_camera_request(filter_name: str, binning: int) -> tuple[str, int]:
    """Normalize filter/binning to an allowed combination for the active camera."""
    profile = _load_equipment_profile()
    if not profile:
        return filter_name, binning
    camera = profile.camera
    allowed_filters = camera.filters or [filter_name]
    chosen_filter = filter_name
    if camera.type.lower() == "osc":
        # For OSC cameras, map any request to the single available filter.
        chosen_filter = allowed_filters[0]
    elif filter_name not in allowed_filters:
        raise HTTPException(
            status_code=422,
            detail={"reason": "unsupported_filter", "allowed_filters": allowed_filters},
        )
    chosen_binning = min(binning, camera.max_binning)
    return chosen_filter, chosen_binning


def _validate_focuser_position(position: int) -> None:
    profile = _load_equipment_profile()
    if not profile or not profile.focuser:
        return
    focuser = profile.focuser
    if not (focuser.position_min <= position <= focuser.position_max):
        raise HTTPException(
            status_code=422,
            detail={
                "reason": "focuser_out_of_range",
                "min": focuser.position_min,
                "max": focuser.position_max,
            },
        )


def _apply_camera_overrides(
    filter_name: str,
    binning: int,
    profile: EquipmentProfile | None,
) -> tuple[str, int]:
    if not profile:
        return filter_name, binning
    camera = profile.camera
    chosen_filter = filter_name
    allowed_filters = camera.filters or [filter_name]
    if camera.type.lower() == "osc":
        chosen_filter = allowed_filters[0]
    elif chosen_filter not in allowed_filters:
        chosen_filter = allowed_filters[0]
    chosen_binning = min(binning, camera.max_binning)
    return chosen_filter, chosen_binning


def _collect_blockers(summary: WeatherSummary | None = None) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if STATE.manual_override:
        blockers.append({"reason": "manual_override"})
    if STATE.dome_closed:
        blockers.append({"reason": "dome_closed"})
    if bridge_settings.require_weather_safe:
        weather_summary = summary or _current_weather()
        if weather_summary and not weather_summary.is_safe:
            blockers.append({"reason": "weather_blocked", "factors": weather_summary.reasons})
    return blockers


def _enforce_safety(action: str, weather_summary: WeatherSummary | None = None) -> None:
    """Ensure safety interlocks are respected before issuing commands."""
    blockers = _collect_blockers(weather_summary)
    if blockers:
        raise HTTPException(status_code=423, detail={"action": action, "blockers": blockers})


async def _sync_sequence_state(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Refresh local sequence state from NINA."""
    try:
        status = await _forward_request(client, "GET", "/sequence/status")
    except HTTPException:
        STATE.sequence_running = False
        STATE.last_sequence_name = None
        return None
    STATE.sequence_running = bool(status.get("is_running"))
    STATE.last_sequence_name = status.get("name")
    return status


async def _activity_status(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch NINA status and update local state."""
    status = await _forward_request(client, "GET", "/status")
    sequence = status.get("sequence") or {}
    STATE.sequence_running = bool(sequence.get("is_running"))
    STATE.last_sequence_name = sequence.get("name")
    return status


async def _ensure_sequence_idle(client: httpx.AsyncClient, action: str) -> None:
    seq_status = await _sync_sequence_state(client)
    if seq_status and seq_status.get("is_running"):
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "sequence_running",
                "action": action,
                "name": seq_status.get("name"),
                "current_index": seq_status.get("current_index"),
            },
        )


async def _ensure_telescope_ready(
    client: httpx.AsyncClient,
    action: str,
    allow_parked: bool = False,
) -> None:
    status = await _activity_status(client)
    telescope = status.get("telescope") or {}
    if not telescope.get("is_connected", True):
        raise HTTPException(status_code=409, detail={"reason": "telescope_disconnected", "action": action})
    if telescope.get("is_parked") and not allow_parked:
        raise HTTPException(status_code=409, detail={"reason": "telescope_parked", "action": action})
    if telescope.get("is_slewing") and action == "telescope_slew":
        raise HTTPException(status_code=409, detail={"reason": "telescope_busy", "action": action})


async def _ensure_camera_idle(client: httpx.AsyncClient, action: str) -> None:
    status = await _activity_status(client)
    camera = status.get("camera") or {}
    if camera.get("is_exposing"):
        raise HTTPException(status_code=409, detail={"reason": "camera_exposing", "action": action})
    await _ensure_sequence_idle(client, action)


def _tracking_mode_for(rate_arcsec_per_min: float | None, template_mode: str) -> str:
    if rate_arcsec_per_min and rate_arcsec_per_min >= 30.0:
        return "target_rate"
    return template_mode


def _plan_from_template(
    payload: SequencePlanRequest,
    profile: EquipmentProfile | None,
) -> SequencePlanResponse:
    template = select_template(payload.vmag, payload.urgency, profile)
    filter_name, binning = _apply_camera_overrides(template.filter, template.binning, profile)
    tracking_mode = _tracking_mode_for(payload.rate_arcsec_per_min, template.tracking_mode)
    return SequencePlanResponse(
        name=template.name,
        filter=filter_name,
        binning=binning,
        count=template.count,
        exposure_seconds=template.exposure_seconds,
        tracking_mode=tracking_mode,
        focus_offset=template.focus_offset,
        gain=template.gain,
        offset=template.offset,
        preset=template.name,
    )


def _extend_blockers_with_activity(blockers: list[dict[str, Any]], nina_status: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not nina_status:
        return blockers
    camera = nina_status.get("camera") or {}
    sequence = nina_status.get("sequence") or {}
    if camera.get("is_exposing"):
        blockers.append({"reason": "camera_exposing"})
    if sequence.get("is_running"):
        blockers.append({"reason": "sequence_running", "name": sequence.get("name")})
    return blockers


def _ready_flags(nina_status: dict[str, Any] | None, blockers: list[dict[str, Any]]) -> dict[str, bool]:
    camera = (nina_status or {}).get("camera") or {}
    telescope = (nina_status or {}).get("telescope") or {}
    sequence = (nina_status or {}).get("sequence") or {}
    is_blocked = bool(blockers)
    return {
        "ready_to_slew": not is_blocked and telescope.get("is_connected", True) and not telescope.get("is_parked") and not telescope.get("is_slewing"),
        "ready_to_expose": not is_blocked and not camera.get("is_exposing") and not sequence.get("is_running"),
        "sequence_running": bool(sequence.get("is_running")),
    }
    if STATE.manual_override:
        raise HTTPException(status_code=423, detail="manual_override_engaged")
    if STATE.dome_closed:
        raise HTTPException(status_code=423, detail="dome_closed")
    if bridge_settings.require_weather_safe:
        summary = _current_weather()
        if summary and not summary.is_safe:
            raise HTTPException(
                status_code=423,
                detail={"reason": "weather_blocked", "factors": summary.reasons},
            )


async def _forward_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    response: httpx.Response | None = None
    for attempt in range(1, bridge_settings.max_retries + 1):
        try:
            response = await client.request(method, path, json=payload)
            response.raise_for_status()
            break
        except httpx.HTTPStatusError as exc:  # pragma: no cover - passthrough
            if exc.response.status_code >= 500 and attempt < bridge_settings.max_retries:
                delay = 0.5 * attempt + random.uniform(0, 0.25)
                logger.warning(
                    "Retrying NINA %s %s after HTTP %s (attempt %s/%s, delay %.2fs)",
                    method,
                    path,
                    exc.response.status_code,
                    attempt,
                    bridge_settings.max_retries,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.warning(
                "NINA API error %s for %s %s: %s",
                exc.response.status_code,
                method,
                path,
                exc.response.text,
            )
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
        except httpx.HTTPError as exc:
            logger.error("Failed to reach NINA API attempt %s/%s: %s", attempt, bridge_settings.max_retries, exc)
            if attempt >= bridge_settings.max_retries:
                raise HTTPException(status_code=502, detail={"reason": "nina_unreachable"})
            delay = 0.5 * attempt + random.uniform(0, 0.25)
            await asyncio.sleep(delay)
    if response is None:
        raise HTTPException(status_code=502, detail={"reason": "nina_unreachable"})
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


@app.get(f"{API_PREFIX}/status", response_model=BridgeStatus)
async def bridge_status(
    client: httpx.AsyncClient = Depends(get_client),
) -> BridgeStatus:
    nina_status = await _forward_request(client, "GET", "/status")
    sequence = nina_status.get("sequence") or {}
    STATE.sequence_running = bool(sequence.get("is_running"))
    STATE.last_sequence_name = sequence.get("name")
    weather_summary = _current_weather()
    weather = _summary_to_dict(weather_summary)
    equipment = _profile_to_dict(_load_equipment_profile())
    blockers = _extend_blockers_with_activity(_collect_blockers(weather_summary), nina_status)
    return BridgeStatus(
        manual_override=STATE.manual_override,
        dome_closed=STATE.dome_closed,
        weather=weather,
        nina_status=nina_status,
        equipment_profile=equipment,
        blockers=blockers,
        ready=_ready_flags(nina_status, blockers),
    )


@app.get(f"{API_PREFIX}/override")
async def get_override_state() -> dict[str, bool]:
    return {"manual_override": STATE.manual_override}


@app.post(f"{API_PREFIX}/override")
async def set_override_state(payload: OverrideUpdate) -> dict[str, bool]:
    STATE.manual_override = payload.manual_override
    logger.info("Manual override set to %s", STATE.manual_override)
    return {"manual_override": STATE.manual_override}


@app.get(f"{API_PREFIX}/dome")
async def get_dome_state() -> dict[str, bool]:
    return {"closed": STATE.dome_closed}


@app.post(f"{API_PREFIX}/dome")
async def set_dome_state(payload: DomeUpdate) -> dict[str, bool]:
    STATE.dome_closed = payload.closed
    logger.info("Dome closed state set to %s", STATE.dome_closed)
    return {"closed": STATE.dome_closed}


@app.get(f"{API_PREFIX}/equipment/profile")
async def equipment_profile() -> dict[str, Any] | None:
    return _profile_to_dict(_load_equipment_profile())


@app.post(f"{API_PREFIX}/telescope/slew")
async def telescope_slew(
    payload: SlewCommand,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="telescope_slew")
    await _ensure_telescope_ready(client, action="telescope_slew")
    await _ensure_sequence_idle(client, action="telescope_slew")
    return await _forward_request(client, "POST", "/telescope/slew", payload.dict())


@app.get(f"{API_PREFIX}/telescope/tracking")
async def get_tracking_mode(client: httpx.AsyncClient = Depends(get_client)) -> Any:
    return await _forward_request(client, "GET", "/telescope/tracking")


@app.post(f"{API_PREFIX}/telescope/tracking")
async def set_tracking_mode(
    payload: TrackingPayload,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="telescope_tracking")
    return await _forward_request(client, "POST", "/telescope/tracking", payload.model_dump())


@app.post(f"{API_PREFIX}/telescope/connect")
async def telescope_connect(
    payload: ConnectionRequest,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    if payload.connect:
        _enforce_safety(action="telescope_connect")
    else:
        await _ensure_sequence_idle(client, action="telescope_disconnect")
    return await _forward_request(client, "POST", "/telescope/connect", payload.dict())


@app.post(f"{API_PREFIX}/telescope/park")
async def telescope_park(
    payload: ParkToggle,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="telescope_park")
    await _ensure_camera_idle(client, action="telescope_park")
    response = await _forward_request(client, "POST", "/telescope/park", payload.dict())
    if payload.park:
        STATE.manual_override = False
    return response


@app.post(f"{API_PREFIX}/camera/exposure")
async def camera_exposure(
    payload: ExposureRequest,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="camera_exposure")
    filter_name, binning = _normalize_camera_request(payload.filter, payload.binning)
    _validate_camera_request(filter_name, binning)
    await _ensure_camera_idle(client, action="camera_exposure")
    await _ensure_telescope_ready(client, action="camera_exposure")
    return await _forward_request(
        client,
        "POST",
        "/camera/start_exposure",
        {**payload.dict(), "filter": filter_name, "binning": binning},
    )


@app.post(f"{API_PREFIX}/sequence/start")
async def sequence_start(
    payload: SequenceRequest,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="sequence_start")
    filter_name, binning = _normalize_camera_request(payload.filter, payload.binning)
    _validate_camera_request(filter_name, binning)
    await _ensure_telescope_ready(client, action="sequence_start")
    await _ensure_sequence_idle(client, action="sequence_start")
    response = await _forward_request(
        client,
        "POST",
        "/sequence/start",
        {**payload.dict(), "filter": filter_name, "binning": binning},
    )
    STATE.sequence_running = True
    STATE.last_sequence_name = payload.name
    return response


@app.post(f"{API_PREFIX}/sequence/cancel")
async def sequence_cancel(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    seq_status = await _sync_sequence_state(client)
    if seq_status and seq_status.get("is_running"):
        try:
            await _forward_request(client, "POST", "/sequence/abort")
        except HTTPException:
            pass
    STATE.sequence_running = False
    STATE.last_sequence_name = None
    return {"status": "idle"}


@app.post(f"{API_PREFIX}/sequence/abort")
async def sequence_abort(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    # Attempt graceful stop if NINA supports it, otherwise mark idle locally.
    try:
        response = await _forward_request(client, "POST", "/sequence/abort")
    except HTTPException:
        response = {"status": "aborted_local"}
    STATE.sequence_running = False
    STATE.last_sequence_name = None
    return response


@app.get(f"{API_PREFIX}/weather")
async def weather_snapshot(force_refresh: bool = False) -> dict[str, Any] | None:
    return _summary_to_dict(_current_weather(force_refresh=force_refresh))


@app.post(f"{API_PREFIX}/focuser/move")
async def focuser_move(
    payload: FocuserMove,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety()
    _validate_focuser_position(payload.position)
    return await _forward_request(client, "POST", "/focuser/move", payload.dict())


@app.get(f"{API_PREFIX}/focuser/status")
async def focuser_status(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/focuser/status")


@app.post(f"{API_PREFIX}/sequence/plan", response_model=SequencePlanResponse)
async def plan_sequence(payload: SequencePlanRequest) -> SequencePlanResponse:
    profile = _load_equipment_profile()
    return _plan_from_template(payload, profile)


@app.get(f"{API_PREFIX}/sequence/status")
async def sequence_status(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    status = await _forward_request(client, "GET", "/sequence/status")
    STATE.sequence_running = bool(status.get("is_running"))
    STATE.last_sequence_name = status.get("name")
    return status
