"""Ephemeral observing session state and calibration progress tracking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, List

from app.services.calibration import CalibrationPlan, nightly_calibration_plan, run_calibration_plan
from app.services.captures import record_capture
from app.services.presets import ExposurePreset


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
    selected_preset: dict[str, Any] | None = None
    target_mode: str = "auto"
    selected_target: str | None = None
    paused: bool = False
    associations: dict[str, dict[str, Any]] = field(default_factory=dict)
    master_calibrations: dict[str, str] = field(default_factory=dict)
    predicted: dict[str, dict[str, Any]] = field(default_factory=dict)

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
            "selected_preset": self.selected_preset,
            "target_mode": self.target_mode,
            "selected_target": self.selected_target,
            "paused": self.paused,
            "associations": self.associations,
            "master_calibrations": self.master_calibrations,
            "predicted": self.predicted,
        }


class SessionState:
    """In-memory tracker for the current observing session."""

    def __init__(self) -> None:
        self.current: ObservingSession | None = None
        self.selected_preset: dict[str, Any] | None = None
        self.target_mode: str = "auto"
        self.selected_target: str | None = None
        self.associations: dict[str, dict[str, Any]] = {}
        self.master_calibrations: dict[str, str] = {}
        self.predicted: dict[str, dict[str, Any]] = {}

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
            selected_preset=self.selected_preset,
            target_mode=self.target_mode,
            selected_target=self.selected_target,
            paused=False,
            associations=self.associations,
            master_calibrations=self.master_calibrations,
            predicted=self.predicted,
        )
        self.current = session
        return session

    def end(self) -> ObservingSession | None:
        if not self.current:
            return None
        session = self.current
        session.ended_at = datetime.utcnow()
        self.current = None
        return session

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

    def select_preset(self, preset: ExposurePreset) -> dict[str, Any]:
        """Record the operator's chosen exposure preset."""
        snapshot = _preset_to_snapshot(preset)
        self.selected_preset = snapshot
        if self.current:
            self.current.selected_preset = snapshot
        return snapshot

    def update_preset_config(
        self,
        *,
        exposure_seconds: float,
        count: int,
        delay_seconds: float,
        binning: int,
        filter_name: str,
    ) -> dict[str, Any]:
        if not self.selected_preset:
            raise ValueError("no_preset_selected")
        snapshot = dict(self.selected_preset)
        snapshot.update(
            {
                "exposure_seconds": exposure_seconds,
                "count": count,
                "delay_seconds": delay_seconds,
                "binning": binning,
                "filter": filter_name,
            }
        )
        snapshot["total_minutes"] = round(
            (count * exposure_seconds + max(0, count - 1) * delay_seconds) / 60.0,
            2,
        )
        self.selected_preset = snapshot
        if self.current:
            self.current.selected_preset = snapshot
        return snapshot

    def set_target_mode(self, mode: str) -> None:
        mode = mode.lower()
        if mode not in {"auto", "manual"}:
            raise ValueError("invalid_mode")
        self.target_mode = mode
        if mode == "auto":
            self.selected_target = None
        if self.current:
            self.current.target_mode = self.target_mode
            self.current.selected_target = self.selected_target

    def select_target(self, trksub: str | None) -> None:
        if trksub:
            self.selected_target = trksub
            self.target_mode = "manual"
        else:
            self.selected_target = None
        if self.current:
            self.current.selected_target = self.selected_target
            self.current.target_mode = self.target_mode
            self.current.associations = self.associations

    def pause(self) -> ObservingSession | None:
        if not self.current:
            return None
        self.current.paused = True
        return self.current

    def resume(self) -> ObservingSession | None:
        if not self.current:
            return None
        self.current.paused = False
        return self.current

    def set_association(self, path: str, ra_deg: float, dec_deg: float) -> dict[str, Any]:
        entry = {"ra_deg": ra_deg, "dec_deg": dec_deg}
        self.associations[path] = entry
        if self.current:
            self.current.associations = self.associations
        return entry

    def set_prediction(self, path: str, ra_deg: float, dec_deg: float) -> dict[str, Any]:
        entry = {"ra_deg": ra_deg, "dec_deg": dec_deg}
        self.predicted[path] = entry
        if self.current:
            self.current.predicted = self.predicted
        return entry

    def set_master(self, cal_type: str, path: str) -> dict[str, str]:
        cal_type = cal_type.lower()
        self.master_calibrations[cal_type] = path
        if self.current:
            self.current.master_calibrations = self.master_calibrations
        return self.master_calibrations


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


def _preset_to_snapshot(preset: ExposurePreset) -> dict[str, Any]:
    data = asdict(preset)
    # Normalize key names for UI clarity
    data["total_minutes"] = round(
        (preset.count * preset.exposure_seconds + max(0, preset.count - 1) * preset.delay_seconds) / 60.0,
        2,
    )
    return data
