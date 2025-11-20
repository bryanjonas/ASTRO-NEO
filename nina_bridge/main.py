"""FastAPI bridge that fronts the real/mock NINA API."""

from __future__ import annotations

import logging
import asyncio
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
)
from nina_bridge.state import STATE
from nina_bridge.templates import select_template

from app.db.session import get_session
from app.services.equipment import EquipmentProfile, get_active_equipment_profile
from app.services.weather import WeatherService, WeatherSummary

logger = logging.getLogger("nina_bridge")

app = FastAPI(title="NINA Bridge", version="0.1.0")
API_PREFIX = "/api"


async def get_client() -> httpx.AsyncClient:
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


def _enforce_safety() -> None:
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
                raise HTTPException(status_code=502, detail="nina_unreachable")
            await asyncio.sleep(0.5 * attempt)
    if response is None:
        raise HTTPException(status_code=502, detail="nina_unreachable")
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        return response.json()
    return response.text


@app.get(f"{API_PREFIX}/status", response_model=BridgeStatus)
async def bridge_status(
    client: httpx.AsyncClient = Depends(get_client),
) -> BridgeStatus:
    nina_status = await _forward_request(client, "GET", "/status")
    weather = _summary_to_dict(_current_weather())
    equipment = _profile_to_dict(_load_equipment_profile())
    return BridgeStatus(
        manual_override=STATE.manual_override,
        dome_closed=STATE.dome_closed,
        weather=weather,
        nina_status=nina_status,
        equipment_profile=equipment,
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
    _enforce_safety()
    return await _forward_request(client, "POST", "/telescope/slew", payload.dict())


@app.post(f"{API_PREFIX}/telescope/connect")
async def telescope_connect(
    payload: ConnectionRequest,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "POST", "/telescope/connect", payload.dict())


@app.post(f"{API_PREFIX}/telescope/park")
async def telescope_park(
    payload: ParkToggle,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety()
    return await _forward_request(client, "POST", "/telescope/park", payload.dict())


@app.post(f"{API_PREFIX}/camera/exposure")
async def camera_exposure(
    payload: ExposureRequest,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety()
    _validate_camera_request(payload.filter, payload.binning)
    return await _forward_request(
        client,
        "POST",
        "/camera/start_exposure",
        payload.dict(),
    )


@app.post(f"{API_PREFIX}/sequence/start")
async def sequence_start(
    payload: SequenceRequest,
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety()
    _validate_camera_request(payload.filter, payload.binning)
    return await _forward_request(client, "POST", "/sequence/start", payload.dict())


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
    template = select_template(payload.vmag)
    filter_name, binning = _apply_camera_overrides(template.filter, template.binning, profile)
    return SequencePlanResponse(
        name=template.name,
        filter=filter_name,
        binning=binning,
        count=template.count,
        exposure_seconds=template.exposure_seconds,
    )
