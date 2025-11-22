"""Calibration frame planning and execution via the bridge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Iterable, Iterator, List, Sequence, TYPE_CHECKING

from fastapi import HTTPException

from app.core.config import settings
from app.services.imaging import build_fits_path
from app.services.nina_bridge import NinaBridgeService
from app.services.notifications import NOTIFICATIONS

if TYPE_CHECKING:  # pragma: no cover
    from app.services.session import SessionCalibration, SessionState


@dataclass
class CalibrationPlan:
    type: str  # "dark", "flat", "bias"
    count: int
    exposure_seconds: float | None = None
    filter: str | None = None


def nightly_calibration_plan(filter_name: str | None = None, exposure_seconds: float | None = None) -> List[CalibrationPlan]:
    """Return a simple nightly calibration plan based on settings."""
    plans: list[CalibrationPlan] = []
    if settings.calibration_dark_counts > 0 and exposure_seconds:
        plans.append(CalibrationPlan(type="dark", count=settings.calibration_dark_counts, exposure_seconds=exposure_seconds))
    if settings.calibration_flat_counts > 0:
        plans.append(CalibrationPlan(type="flat", count=settings.calibration_flat_counts, filter=filter_name or "L"))
    if settings.calibration_bias_counts > 0:
        plans.append(CalibrationPlan(type="bias", count=settings.calibration_bias_counts, exposure_seconds=0.0))
    return plans


def calibration_output_path(cal_type: str, started_at: datetime, index: int) -> str:
    """Build a path under /data/fits/calibration/<type>/... for the given calibration frame."""
    root_name = f"cal_{cal_type}"
    path = build_fits_path(root_name, started_at, sequence_name=cal_type, index=index)
    return str(path)


def _wait_ready(bridge: NinaBridgeService, timeout: float = 60.0, interval: float = 0.5) -> None:
    """Poll bridge status until ready_to_expose is true or timeout."""
    deadline = datetime.utcnow().timestamp() + timeout
    while datetime.utcnow().timestamp() < deadline:
        status = bridge.status()
        blockers = status.get("blockers") or []
        ready = (status.get("ready") or {}).get("ready_to_expose")

        # Treat transient activity blockers as waitable; surface hard blockers immediately.
        fatal_blockers = [b for b in blockers if b.get("reason") not in {"camera_exposing", "sequence_running"}]
        if fatal_blockers:
            raise HTTPException(status_code=423, detail={"reason": "blocked", "blockers": fatal_blockers})
        if ready:
            return
        sleep(interval)
    raise HTTPException(status_code=408, detail="calibration_wait_timeout")


def _effective_exposure_seconds(cal: "SessionCalibration") -> float:
    if cal.type == "bias":
        return 0.001
    if cal.exposure_seconds is None or cal.exposure_seconds <= 0:
        return 1.0
    return cal.exposure_seconds


def run_calibration_plan(session_state: "SessionState", bridge: NinaBridgeService | None = None) -> dict:
    """Execute remaining calibrations in the active session via the bridge."""
    svc = bridge or NinaBridgeService()
    session = session_state.current or session_state.start(notes="auto-calibration")
    captures: list[dict] = []

    for cal in session.calibrations:
        remaining = cal.remaining
        if remaining <= 0:
            continue
        exposure_seconds = _effective_exposure_seconds(cal)
        filt = cal.filter or "L"
        for idx in range(1, remaining + 1):
            _wait_ready(svc, timeout=120)
            started_at = datetime.utcnow()
            svc.start_exposure(filter_name=filt, binning=1, exposure_seconds=exposure_seconds)
            # wait for exposure to clear
            _wait_ready(svc, timeout=exposure_seconds + 30)
            cal.completed = min(cal.required, cal.completed + 1)
            captures.append(
                {
                    "type": cal.type,
                    "index": cal.completed,
                    "expected_path": calibration_output_path(cal.type, started_at, cal.completed),
                    "filter": filt,
                    "exposure_seconds": exposure_seconds,
                }
            )
        NOTIFICATIONS.add(
            "info",
            f"Calibration {cal.type} frames completed ({cal.completed}/{cal.required})",
            {"filter": filt, "exposure_seconds": exposure_seconds},
        )

    if session_state.current:
        session_state.current.captures.extend(captures)

    return {"session": session.to_dict(), "captures": captures}


__all__ = ["CalibrationPlan", "nightly_calibration_plan", "calibration_output_path", "run_calibration_plan"]
