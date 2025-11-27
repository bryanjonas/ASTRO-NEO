"""Session and calibration tracking endpoints (ephemeral)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from app.services.nina_bridge import NinaBridgeService
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
        SESSION_STATE.end()
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


@router.get("/dashboard/status")
def dashboard_status() -> Any:
    """Bundle bridge + session info for a lightweight dashboard poll."""

    bridge = NinaBridgeService()
    bridge_status = bridge.get_status()
    session_info = SESSION_STATE.current.to_dict() if SESSION_STATE.current else None
    return {
        "bridge_blockers": bridge_status.get("blockers"),
        "bridge_ready": bridge_status.get("ready"),
        "bridge_status": bridge_status.get("nina_status"),
        "ignore_weather": bridge_status.get("ignore_weather"),
        "session": session_info,
        "notifications": SESSION_STATE.log,
    }
