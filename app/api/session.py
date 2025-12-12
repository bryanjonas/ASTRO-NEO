"""Session and calibration tracking endpoints (ephemeral)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services.nina_client import NinaBridgeService
from app.services.night_ops import NightSessionError, kickoff_imaging
from app.services.session import SESSION_STATE
from datetime import datetime

router = APIRouter(prefix="/session", tags=["session"])


class SessionStartPayload(BaseModel):
    notes: str | None = Field(default=None, max_length=500)
    calibration_filter: str | None = Field(default=None, max_length=16)
    calibration_exposure_seconds: float | None = Field(default=None, gt=0)


class CalibrationRecordPayload(BaseModel):
    type: str = Field(..., min_length=1, max_length=16)
    count: int = Field(default=1, ge=1, le=50)


class CalibrationResetPayload(BaseModel):
    type: str | None = Field(default=None, min_length=1, max_length=16, description="Reset a single type or all when omitted.")


class SessionPausePayload(BaseModel):
    pause: bool = Field(default=True)


class CaptureIn(BaseModel):
    kind: str = Field(default="synthetic", max_length=32)
    target: str = Field(..., max_length=128)
    sequence: str | None = Field(default=None, max_length=128)
    index: int | None = Field(default=None)
    path: str = Field(..., max_length=512)
    started_at: datetime = Field(...)
    predicted_ra_deg: float | None = Field(default=None)
    predicted_dec_deg: float | None = Field(default=None)


class TargetSequencePayload(BaseModel):
    """Payload for starting a sequential target sequence."""
    name: str | None = Field(default=None, max_length=128)
    target_ids: list[str] = Field(..., min_items=1, max_items=20, description="List of NEOCP target IDs")
    park_after: bool = Field(default=False)


@router.post("/calibration/run")
def calibration_run() -> Any:
    if not SESSION_STATE.current:
        SESSION_STATE.start(notes="auto-calibration")
    result = SESSION_STATE.run_calibrations()
    return {"active": True, "session": result.get("session"), "captures": result.get("captures")}


@router.get("/status")
def session_status() -> Any:
    if not SESSION_STATE.current:
        return {"active": False}
    return {"active": True, "session": SESSION_STATE.current.to_dict()}


@router.post("/start")
def session_start(payload: SessionStartPayload | None = Body(None)) -> Any:
    if payload:
        session = SESSION_STATE.start(
            notes=payload.notes,
            calibration_filter=payload.calibration_filter,
            calibration_exposure_seconds=payload.calibration_exposure_seconds,
        )
    else:
        session = SESSION_STATE.start()
    try:
        automation = kickoff_imaging()
    except NightSessionError as exc:
        SESSION_STATE.end(reason=exc.message)
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    return {"active": True, "session": session.to_dict(), "automation": automation}


@router.post("/end")
def session_end() -> Any:
    session = SESSION_STATE.end()
    if not session:
        raise HTTPException(status_code=404, detail="no_active_session")
    return {"active": False, "session": session.to_dict()}


@router.post("/calibration/record")
def calibration_record(payload: CalibrationRecordPayload) -> Any:
    session = SESSION_STATE.record_calibration(payload.type, payload.count)
    if not session:
        raise HTTPException(status_code=404, detail="no_active_session")
    return {"active": True, "session": session.to_dict()}


@router.post("/calibration/reset")
def calibration_reset(payload: CalibrationResetPayload | None = Body(None)) -> Any:
    if not SESSION_STATE.current:
        raise HTTPException(status_code=404, detail="no_active_session")
    reset_type = payload.type if payload else None
    SESSION_STATE.reset_calibrations(reset_type)
    return {"active": True, "session": SESSION_STATE.current.to_dict()}


@router.post("/pause")
def session_pause(payload: SessionPausePayload | None = Body(None)) -> Any:
    if not SESSION_STATE.current:
        raise HTTPException(status_code=404, detail="no_active_session")
    pause = True if payload is None else payload.pause
    session = SESSION_STATE.pause() if pause else SESSION_STATE.resume()
    return {"active": True, "session": session.to_dict()}


@router.post("/ingest_captures")
def ingest_captures(captures: list[CaptureIn]) -> Any:
    """Inject captures into the in-memory session for association/solver workflows."""
    if not SESSION_STATE.current:
        SESSION_STATE.start(notes="synthetic-ingest")
    payloads: list[dict[str, Any]] = []
    for cap in captures:
        payloads.append(
            {
                "kind": cap.kind,
                "target": cap.target,
                "sequence": cap.sequence,
                "index": cap.index,
                "path": cap.path,
                "started_at": cap.started_at.isoformat(),
            }
        )
        if cap.predicted_ra_deg is not None and cap.predicted_dec_deg is not None:
            SESSION_STATE.set_prediction(cap.path, cap.predicted_ra_deg, cap.predicted_dec_deg)
    SESSION_STATE.add_captures(payloads)
    return {"active": True, "session": SESSION_STATE.current.to_dict(), "count": len(payloads)}


@router.post("/sequence")
def start_sequence(payload: TargetSequencePayload) -> Any:
    """
    Start sequential observations of the requested targets.

    This endpoint processes targets ONE AT A TIME:
    1. Fetches target data from the database
    2. Builds a sequential plan with presets for each target
    3. For each target sequentially:
       a. Sends single-target sequence to NINA
       b. Waits for all images from that target
       c. Plate-solves any images NINA didn't solve
       d. Moves to next target
    4. Optionally parks telescope when all targets complete
    """
    from app.db.session import get_session
    from app.models import NeoCandidate
    from app.services.automation import AutomationService
    from sqlmodel import select

    # Start a session if not already active
    if not SESSION_STATE.current:
        SESSION_STATE.start(notes="sequential-target-sequence")

    # Fetch target data from database
    targets_data = []
    with get_session() as session:
        for target_id in payload.target_ids:
            candidate = session.exec(
                select(NeoCandidate).where(NeoCandidate.id == target_id)
            ).first()

            if not candidate:
                raise HTTPException(
                    status_code=404,
                    detail=f"Target not found: {target_id}"
                )

            targets_data.append({
                "name": candidate.id,
                "ra_deg": candidate.ra_deg,
                "dec_deg": candidate.dec_deg,
                "vmag": candidate.vmag,
                "candidate_id": candidate.id,
            })

    if not targets_data:
        raise HTTPException(
            status_code=400,
            detail="No valid targets found"
        )

    # Build and execute sequential plan
    automation = AutomationService()
    plan = automation.build_sequential_target_plan(
        targets=targets_data,
        name=payload.name,
        park_after=payload.park_after,
    )

    try:
        result = automation.run_sequential_target_sequence(plan)
        return {
            "success": True,
            "sequence": result,
            "targets_count": len(targets_data),
        }
    except Exception as e:
        SESSION_STATE.log_event(f"Failed to start sequence: {e}", "error")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start sequence: {str(e)}"
        )


@router.get("/dashboard/status")
def dashboard_status() -> Any:
    """Bundle bridge + session info for a lightweight dashboard poll."""

    bridge = NinaBridgeService()
    bridge_status = bridge.get_status()
    session_info = SESSION_STATE.current.to_dict() if SESSION_STATE.current else None

    # Fetch local weather status
    from app.services.weather import WeatherService
    from app.db.session import get_session
    weather_summary = None
    with get_session() as session:
        weather_service = WeatherService(session)
        weather_summary = weather_service.get_status()

    return {
        "bridge_blockers": bridge_status.get("blockers"),
        "bridge_ready": bridge_status.get("ready"),
        "bridge_status": bridge_status.get("nina_status"),
        "ignore_weather": bridge_status.get("ignore_weather"),
        "session": session_info,
        "notifications": SESSION_STATE.log,
        "weather_summary": weather_summary,
        "target_available": _check_target_availability(),
    }


def _check_target_availability() -> str | None:
    from app.services.night_ops import _fetch_target_internal
    try:
        # Check availability ignoring the 'current time' constraint, 
        # so the indicator reflects if there are ANY valid targets for the configured window.
        target_now = _fetch_target_internal(ignore_time=False)
        if target_now:
            return "Available"
            
        target_any = _fetch_target_internal(ignore_time=True)
        if target_any:
            # If we found a target but it's not available NOW, then we are waiting.
            # However, _fetch_target_internal(ignore_time=True) returns the BEST target in the window.
            # If that target is also available now, target_now would have caught it.
            # So if we are here, target_now is None, meaning the best target is NOT available now.
            start_dt = target_any.get("window_start")
            if start_dt:
                from datetime import timezone
                import zoneinfo
                if not start_dt.tzinfo:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
                try:
                    tz = zoneinfo.ZoneInfo(SESSION_STATE.timezone)
                    start_dt = start_dt.astimezone(tz)
                except Exception:
                    pass
                start_str = start_dt.strftime("%H:%M")
            else:
                start_str = "window"
            return f"Waiting for {start_str}"
            
        return "None (No observable targets)"
    except Exception as exc:
        import logging
        logging.getLogger("uvicorn").error(f"Target availability check failed: {exc}")
        # Extract message from exception if possible, or generic error
        msg = str(exc)
        if "No visible targets available" in msg:
             return "None (No visible targets)"
        if "No targets are currently observable" in msg:
             return "None (Check window/weather)"
        return "None (Error)"
