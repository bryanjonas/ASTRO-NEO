"""Ephemeral observing session state and calibration progress tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from app.services.calibration import CalibrationPlan, nightly_calibration_plan, run_calibration_plan
from app.services.captures import record_capture


@dataclass
class SessionCalibration:
    type: str
    required: int
    completed: int = 0
    exposure_seconds: float | None = None
    filter: str | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.required - self.completed)


@dataclass
class ObservingSession:
    started_at: datetime
    notes: str | None = None
    ended_at: datetime | None = None
    calibrations: List[SessionCalibration] = field(default_factory=list)
    captures: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "notes": self.notes,
            "calibrations": [
                {
                    "type": cal.type,
                    "required": cal.required,
                    "completed": cal.completed,
                    "remaining": cal.remaining,
                    "exposure_seconds": cal.exposure_seconds,
                    "filter": cal.filter,
                }
                for cal in self.calibrations
            ],
            "captures": self.captures,
        }


class SessionState:
    """In-memory tracker for the current observing session."""

    def __init__(self) -> None:
        self.current: ObservingSession | None = None

    def start(
        self,
        notes: str | None = None,
        calibration_filter: str | None = None,
        calibration_exposure_seconds: float | None = None,
    ) -> ObservingSession:
        plan = nightly_calibration_plan(filter_name=calibration_filter, exposure_seconds=calibration_exposure_seconds)
        session = ObservingSession(
            started_at=datetime.utcnow(),
            notes=notes,
            calibrations=_plan_to_calibrations(plan),
        )
        self.current = session
        return session

    def end(self) -> ObservingSession | None:
        if not self.current:
            return None
        self.current.ended_at = datetime.utcnow()
        return self.current

    def record_calibration(self, cal_type: str, count: int = 1) -> ObservingSession | None:
        if not self.current:
            return None
        for cal in self.current.calibrations:
            if cal.type == cal_type:
                cal.completed = min(cal.required, cal.completed + count)
                break
        return self.current

    def reset_calibrations(self, cal_type: str | None = None) -> ObservingSession | None:
        """Reset calibration counts for a given type, or all if type is None."""
        if not self.current:
            return None
        for cal in self.current.calibrations:
            if cal_type and cal.type != cal_type:
                continue
            cal.completed = 0
        return self.current

    def run_calibrations(self) -> dict:
        """Execute remaining calibrations via the bridge."""
        return run_calibration_plan(self)

    def add_capture(self, entry: dict) -> None:
        if not self.current:
            self.start()
        assert self.current  # for type checkers
        self.current.captures.append(entry)
        try:
            record_capture(entry)
        except Exception:
            # Persistence errors should not block capture logging.
            pass

    def add_captures(self, entries: List[dict]) -> None:
        for entry in entries:
            self.add_capture(entry)


def _plan_to_calibrations(plan: list[CalibrationPlan]) -> list[SessionCalibration]:
    return [
        SessionCalibration(
            type=item.type,
            required=item.count,
            completed=0,
            exposure_seconds=item.exposure_seconds,
            filter=item.filter,
        )
        for item in plan
    ]


SESSION_STATE = SessionState()

__all__ = ["ObservingSession", "SessionCalibration", "SessionState", "SESSION_STATE"]
