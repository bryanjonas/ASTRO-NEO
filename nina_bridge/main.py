"""FastAPI bridge that fronts the real/mock NINA API."""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path
from datetime import datetime
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query

from nina_bridge.config import settings as bridge_settings
from nina_bridge.models import (
    BridgeStatus,
    DomeUpdate,
    NinaResponse,
    OverrideUpdate,
    IgnoreWeatherUpdate,
    SequencePlanRequest,
    SequencePlanResponse,
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
    """Yield a scoped HTTP client for outbound NINA calls."""
    async with httpx.AsyncClient(
        base_url=bridge_settings.nina_base_url,
        timeout=bridge_settings.http_timeout,
    ) as client:
        yield client


def _success(response_data: Any) -> NinaResponse[Any]:
    return NinaResponse(Response=response_data)


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


def _collect_blockers(summary: WeatherSummary | None = None) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    if STATE.manual_override:
        blockers.append({"reason": "manual_override"})
    if STATE.dome_closed:
        blockers.append({"reason": "dome_closed"})
    if bridge_settings.require_weather_safe and not STATE.ignore_weather:
        weather_summary = summary or _current_weather()
        if weather_summary and not weather_summary.is_safe:
            blockers.append({"reason": "weather_blocked", "factors": weather_summary.reasons})
    return blockers


def _enforce_safety(action: str, weather_summary: WeatherSummary | None = None) -> None:
    """Ensure safety interlocks are respected before issuing commands."""
    blockers = _collect_blockers(weather_summary)
    if blockers:
        raise HTTPException(status_code=423, detail={"action": action, "blockers": blockers})


async def _forward_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    unwrap: bool = False,
) -> Any:
    """Forward request to NINA.
    
    If unwrap=True, returns the inner Response object (for internal use).
    If unwrap=False, returns the full NINA envelope (for proxying).
    """
    logger.info("NINA REQUEST: %s %s | params=%s | json=%s", method, path, params, json)
    
    response: httpx.Response | None = None
    for attempt in range(1, bridge_settings.max_retries + 1):
        try:
            response = await client.request(method, path, params=params, json=json)
            response.raise_for_status()
            break
        except httpx.HTTPStatusError as exc:  # pragma: no cover - passthrough
            if exc.response.status_code >= 500 and attempt < bridge_settings.max_retries:
                delay = 0.5 * attempt + random.uniform(0, 0.25)
                await asyncio.sleep(delay)
                continue
            logger.error("NINA ERROR: %s %s | status=%s | body=%s", method, path, exc.response.status_code, exc.response.text)
            raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text)
        except httpx.HTTPError as exc:
            if attempt >= bridge_settings.max_retries:
                logger.error("NINA UNREACHABLE: %s %s | error=%s", method, path, str(exc))
                raise HTTPException(status_code=502, detail={"reason": "nina_unreachable"})
            delay = 0.5 * attempt + random.uniform(0, 0.25)
            await asyncio.sleep(delay)
    if response is None:
        raise HTTPException(status_code=502, detail={"reason": "nina_unreachable"})
    
    data = response.json()
    logger.info("NINA RESPONSE: %s %s | status=%s | body=%s", method, path, response.status_code, data)
    
    if unwrap:
        # Unwrap NINA envelope if present
        if isinstance(data, dict) and "Success" in data:
            if not data["Success"]:
                 # If we are unwrapping for internal use, we might want to raise or return None
                 # For now, let's return empty dict or raise if critical
                 return {}
            return data.get("Response")
        return data
        
    return data


# --- Custom Bridge Endpoints ---

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


@app.get(f"{API_PREFIX}/status", response_model=NinaResponse[BridgeStatus])
async def bridge_status(
    client: httpx.AsyncClient = Depends(get_client),
) -> NinaResponse[BridgeStatus]:
    # Fetch status from multiple real NINA endpoints
    try:
        # Execute requests in parallel
        results = await asyncio.gather(
            _forward_request(client, "GET", "/equipment/camera/info", unwrap=True),
            _forward_request(client, "GET", "/equipment/mount/info", unwrap=True),
            _forward_request(client, "GET", "/equipment/focuser/info", unwrap=True),
            _forward_request(client, "GET", "/sequence/json", unwrap=True),
            return_exceptions=True
        )
        
        # Process results
        camera_info = results[0] if not isinstance(results[0], Exception) else {}
        mount_info = results[1] if not isinstance(results[1], Exception) else {}
        focuser_info = results[2] if not isinstance(results[2], Exception) else {}
        sequence_info = results[3] if not isinstance(results[3], Exception) else {}

        # Handle sequence_info being a list (SequenceBaseJson returns an array)
        is_sequence_running = False
        if isinstance(sequence_info, list):
            # Check if any container or item is running
            for item in sequence_info:
                if item.get("Status") == "RUNNING" or item.get("IsRunning"):
                    is_sequence_running = True
                    break
        elif isinstance(sequence_info, dict):
            is_sequence_running = sequence_info.get("IsRunning", False) or sequence_info.get("Running", False)

        # Map to the structure expected by the frontend/bridge logic
        nina_status = {
            "camera": {
                "is_connected": camera_info.get("Connected", False),
                "temperature": camera_info.get("Temperature", 0.0),
                "is_exposing": camera_info.get("IsExposing", False),
            },
            "telescope": {
                "is_connected": mount_info.get("Connected", False),
                "is_parked": mount_info.get("AtPark", False),
                "is_slewing": mount_info.get("Slewing", False),
                "ra": mount_info.get("RightAscension", 0.0),
                "dec": mount_info.get("Declination", 0.0),
                "az": mount_info.get("Azimuth", 0.0),
                "alt": mount_info.get("Altitude", 0.0),
                # Frontend expects ra_deg and dec_deg
                # NINA API returns RightAscension in hours usually, but MountInfo.Coordinates.RADegrees is explicit.
                # Let's try to get it from Coordinates object if present, else fallback.
                "ra_deg": mount_info.get("Coordinates", {}).get("RADegrees") if mount_info.get("Coordinates") else (mount_info.get("RightAscension", 0.0) * 15),
                "dec_deg": mount_info.get("Coordinates", {}).get("Dec") if mount_info.get("Coordinates") else mount_info.get("Declination", 0.0),
                "tracking_mode": mount_info.get("TrackingMode", "Stopped"),
            },
            "focuser": {
                "is_connected": focuser_info.get("Connected", False),
                "position": focuser_info.get("Position", 0),
            },
            "sequence": {
                "is_running": is_sequence_running,
                # Extract name from sequence info if available
                "name": sequence_info.get("Name") if isinstance(sequence_info, dict) else None,
                "total": sequence_info.get("TotalItems") if isinstance(sequence_info, dict) else 0,
                "current_index": sequence_info.get("CurrentItemIndex") if isinstance(sequence_info, dict) else 0,
            }
        }
        
    except Exception as exc:
        logger.error("Error aggregating status: %s", exc)
        nina_status = {}
        
    weather_summary = _current_weather()
    weather = _summary_to_dict(weather_summary)
    equipment = _profile_to_dict(_load_equipment_profile())
    blockers = _collect_blockers(weather_summary)
    
    status = BridgeStatus(
        manual_override=STATE.manual_override,
        ignore_weather=STATE.ignore_weather,
        dome_closed=STATE.dome_closed,
        weather=weather,
        nina_status=nina_status,
        equipment_profile=equipment,
        blockers=blockers,
        ready=_ready_flags(nina_status, blockers),
    )
    return _success(status)


@app.get(f"{API_PREFIX}/override")
async def get_override_state() -> NinaResponse[dict[str, bool]]:
    return _success({"manual_override": STATE.manual_override})


@app.post(f"{API_PREFIX}/override")
async def set_override_state(payload: OverrideUpdate) -> NinaResponse[dict[str, bool]]:
    STATE.manual_override = payload.manual_override
    logger.info("Manual override set to %s", STATE.manual_override)
    return _success({"manual_override": STATE.manual_override})


@app.get(f"{API_PREFIX}/ignore_weather")
async def get_ignore_weather_state() -> NinaResponse[dict[str, bool]]:
    return _success({"ignore_weather": STATE.ignore_weather})


@app.post(f"{API_PREFIX}/ignore_weather")
async def set_ignore_weather_state(payload: IgnoreWeatherUpdate) -> NinaResponse[dict[str, bool]]:
    STATE.ignore_weather = payload.ignore_weather
    logger.info("Ignore weather set to %s", STATE.ignore_weather)
    return _success({"ignore_weather": STATE.ignore_weather})


@app.get(f"{API_PREFIX}/dome")
async def get_dome_state() -> NinaResponse[dict[str, bool]]:
    return _success({"closed": STATE.dome_closed})


@app.post(f"{API_PREFIX}/dome")
async def set_dome_state(payload: DomeUpdate) -> NinaResponse[dict[str, bool]]:
    STATE.dome_closed = payload.closed
    logger.info("Dome closed state set to %s", STATE.dome_closed)
    return _success({"closed": STATE.dome_closed})


@app.get(f"{API_PREFIX}/equipment/profile")
async def equipment_profile() -> NinaResponse[dict[str, Any] | None]:
    return _success(_profile_to_dict(_load_equipment_profile()))


@app.post(f"{API_PREFIX}/sequence/plan", response_model=NinaResponse[SequencePlanResponse])
async def plan_sequence(payload: SequencePlanRequest) -> NinaResponse[SequencePlanResponse]:
    profile = _load_equipment_profile()
    # Reuse existing logic but adapted if needed
    template = select_template(payload.vmag, payload.urgency, profile)
    plan = SequencePlanResponse(
        name=template.name,
        filter=template.filter, # simplified
        binning=template.binning,
        count=template.count,
        exposure_seconds=template.exposure_seconds,
        tracking_mode=template.tracking_mode,
        focus_offset=template.focus_offset,
        gain=template.gain,
        offset=template.offset,
        preset=template.name,
    )
    return _success(plan)


@app.post(f"{API_PREFIX}/sequence/start")
async def sequence_start(
    payload: dict[str, Any],
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    # Forward to NINA
    return await _forward_request(client, "POST", "/sequence/start", json=payload)


@app.get(f"{API_PREFIX}/sequence/stop")
async def sequence_stop(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    # Forward to NINA
    return await _forward_request(client, "GET", "/sequence/stop")


@app.get(f"{API_PREFIX}/weather")
async def weather_snapshot(force_refresh: bool = False) -> NinaResponse[dict[str, Any] | None]:
    return _success(_summary_to_dict(_current_weather(force_refresh=force_refresh)))


# --- Proxied Equipment Endpoints (NINA API Compatible) ---

@app.get(f"{API_PREFIX}/equipment/mount/connect")
async def mount_connect(
    to: str | None = Query(None, alias="to"),
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="mount_connect")
    params = {}
    if to:
        params["to"] = to
    return await _forward_request(client, "GET", "/equipment/mount/connect", params=params)


@app.get(f"{API_PREFIX}/equipment/mount/disconnect")
async def mount_disconnect(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/mount/disconnect")


@app.get(f"{API_PREFIX}/equipment/mount/park")
async def mount_park(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="mount_park")
    return await _forward_request(client, "GET", "/equipment/mount/park")


@app.get(f"{API_PREFIX}/equipment/mount/unpark")
async def mount_unpark(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="mount_unpark")
    return await _forward_request(client, "GET", "/equipment/mount/unpark")


@app.get(f"{API_PREFIX}/equipment/mount/slew")
async def mount_slew(
    ra: float = Query(..., alias="ra"),
    dec: float = Query(..., alias="dec"),
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="mount_slew")
    return await _forward_request(client, "GET", "/equipment/mount/slew", params={"ra": ra, "dec": dec})


@app.get(f"{API_PREFIX}/equipment/mount/tracking")
async def mount_set_tracking(
    mode: int = Query(..., alias="mode"),
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="mount_tracking")
    return await _forward_request(client, "GET", "/equipment/mount/tracking", params={"mode": mode})


@app.get(f"{API_PREFIX}/equipment/mount/list-devices")
async def list_mount_devices(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/mount/list-devices")


@app.get(f"{API_PREFIX}/equipment/camera/capture")
async def camera_capture(
    duration: float = Query(..., alias="duration"),
    binning: int = Query(1, alias="binning"),
    download: bool = Query(True, alias="download"),
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="camera_capture")
    
    # 1. Set binning
    await _forward_request(client, "GET", "/equipment/camera/set-binning", params={"binning": f"{binning}x{binning}"})
    
    # 2. Prepare capture parameters
    params = {
        "duration": duration,
        "save": "true", # Always tell NINA to save its own copy too
    }
    
    if download:
        params["stream"] = "true"
        params["waitForResult"] = "true"
    else:
        params["omitImage"] = "true"
        params["waitForResult"] = "false"

    # 3. Execute capture
    # We need to handle the response manually if downloading, as _forward_request expects JSON
    if download:
        logger.info("Starting capture with download: %s", params)
        # We use the client directly here to handle binary response
        try:
            response = await client.get("/equipment/camera/capture", params=params, timeout=duration + 30.0)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                # NINA returned JSON, likely an error or metadata
                return response.json()
            
            # It's an image! Save it.
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            filename = f"capture_{timestamp}.fits" # Assuming FITS for now, NINA usually sends what you ask or default
            # TODO: We might want to inspect headers or request specific format if NINA supports it via params
            
            save_dir = Path("/data/images")
            save_dir.mkdir(parents=True, exist_ok=True)
            file_path = save_dir / filename
            
            with open(file_path, "wb") as f:
                f.write(response.content)
                
            logger.info("Saved captured image to %s", file_path)
            
            return {
                "Success": True,
                "Message": "Image captured and downloaded",
                "File": str(file_path),
                "Type": "NINA_API"
            }
            
        except httpx.TimeoutException:
            logger.error("Capture timed out")
            raise HTTPException(status_code=504, detail="Capture timed out")
        except Exception as exc:
            logger.error("Capture failed: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc))
            
    else:
        # Fire and forget (or wait for start)
        return await _forward_request(client, "GET", "/equipment/camera/capture", params=params)


@app.get(f"{API_PREFIX}/equipment/camera/abort-exposure")
async def camera_abort(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/camera/abort-exposure")


@app.get(f"{API_PREFIX}/equipment/focuser/move")
async def focuser_move(
    position: int = Query(..., alias="position"),
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="focuser_move")
    return await _forward_request(client, "GET", "/equipment/focuser/move", params={"position": position})


@app.get(f"{API_PREFIX}/equipment/focuser/info")
async def focuser_info(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/focuser/info")


@app.get(f"{API_PREFIX}/equipment/dome/connect")
async def dome_connect(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/dome/connect")


@app.get(f"{API_PREFIX}/equipment/dome/open")
async def dome_open(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="dome_open")
    return await _forward_request(client, "GET", "/equipment/dome/open")


@app.get(f"{API_PREFIX}/equipment/camera/list-devices")
async def list_camera_devices(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/camera/list-devices")


@app.get(f"{API_PREFIX}/equipment/camera/connect")
async def camera_connect(
    to: str | None = Query(None, alias="to"),
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    params = {}
    if to:
        params["to"] = to
    return await _forward_request(client, "GET", "/equipment/camera/connect", params=params)


@app.get(f"{API_PREFIX}/equipment/camera/disconnect")
async def camera_disconnect(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/camera/disconnect")


@app.put(f"{API_PREFIX}/telescope/center")
async def telescope_center(
    payload: dict[str, Any],
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="telescope_center")
    # Payload should contain "ra" and "dec"
    return await _forward_request(client, "PUT", "/telescope/center", json=payload)


@app.get(f"{API_PREFIX}/equipment/dome/close")
async def dome_close(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/equipment/dome/close")


from nina_bridge.sequence_builder import build_nina_sequence

@app.post(f"{API_PREFIX}/sequence/start")
async def sequence_start(
    payload: dict[str, Any],
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    _enforce_safety(action="sequence_start")
    
    # 1. Build NINA-compatible sequence JSON
    nina_sequence = build_nina_sequence(
        name=payload.get("name", "Sequence"),
        target=payload.get("target"),
        count=payload.get("count", 1),
        filter_name=payload.get("filter", "L"),
        binning=payload.get("binning", 1),
        exposure_seconds=payload.get("exposure_seconds", 1.0),
        tracking_mode=payload.get("tracking_mode")
    )
    
    # 2. Load the sequence (POST /sequence/load)
    await _forward_request(client, "POST", "/sequence/load", json=nina_sequence)
    
    # 3. Start the sequence (GET /sequence/start)
    return await _forward_request(client, "GET", "/sequence/start")


@app.get(f"{API_PREFIX}/sequence/stop")
async def sequence_stop(
    client: httpx.AsyncClient = Depends(get_client),
) -> Any:
    return await _forward_request(client, "GET", "/sequence/stop")
