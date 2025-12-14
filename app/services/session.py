"""Ephemeral observing session state and calibration progress tracking."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, List, Optional

from sqlmodel import select

from app.db.session import get_session
from app.models.session import ObservingSession as DBObservingSession, SystemEvent
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
    selected_target_ra: float | None = None
    selected_target_dec: float | None = None
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
            "selected_target_ra": self.selected_target_ra,
            "selected_target_dec": self.selected_target_dec,
            "paused": self.paused,
            "associations": self.associations,
            "master_calibrations": self.master_calibrations,
            "predicted": self.predicted,
        }


class SessionState:
    """Database-backed tracker for the observing session."""

    def __init__(self) -> None:
        self._stop_auto_restart = False

    @property
    def current(self) -> ObservingSession | None:
        self.clear_stop_auto_restart()
        with get_session() as session:
            # Find active session
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                return None
            
            return self._to_view(db_session, session)

    @property
    def log(self) -> list[dict[str, str]]:
        tz_name = self.timezone
        from datetime import timezone
        import zoneinfo
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            tz = timezone.utc

        with get_session() as session:
            events = session.exec(
                select(SystemEvent)
                .order_by(SystemEvent.created_at.desc())
                .limit(50)
            ).all()
            results = []
            for e in events:
                dt = e.created_at
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
                local_dt = dt.astimezone(tz)
                results.append({
                    "created_at": local_dt.strftime("%H:%M:%S"),
                    "message": e.message,
                    "level": e.level,
                })
            return results

            return results

    @property
    def window_start(self) -> str | None:
        with get_session() as session:
            # Get from latest session (active or ended)
            db_session = session.exec(
                select(DBObservingSession).order_by(DBObservingSession.start_time.desc())
            ).first()
            return db_session.window_start if db_session else None

    @property
    def window_end(self) -> str | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession).order_by(DBObservingSession.start_time.desc())
            ).first()
            return db_session.window_end if db_session else None

    @property
    def timezone(self) -> str:
        from app.models import SiteConfig
        with get_session() as session:
            active = session.exec(select(SiteConfig).where(SiteConfig.is_active == True)).first()
            return active.timezone if active else "UTC"

    # Helper to convert DB model to View Model
    def _to_view(self, db_session: DBObservingSession, session: Any = None) -> ObservingSession:
        stats = db_session.stats or {}
        config = db_session.config_snapshot or {}
        
        # Reconstruct calibrations
        cal_data = stats.get("calibrations", [])
        calibrations = [
            SessionCalibration(
                type=c["type"],
                required=c["required"],
                completed=c["completed"],
                exposure_seconds=c.get("exposure_seconds"),
                filter=c.get("filter"),
            )
            for c in cal_data
        ]

        # Fetch coordinates if target is selected
        ra = None
        dec = None
        if db_session.selected_target:
            from app.models.neocp import NeoCandidate
            # We need a session to query coordinates. Use provided or create new.
            if session:
                cand = session.exec(select(NeoCandidate).where(NeoCandidate.id == db_session.selected_target)).first()
                if cand:
                    ra = cand.ra_deg
                    dec = cand.dec_deg
            else:
                with get_session() as temp_session:
                    cand = temp_session.exec(select(NeoCandidate).where(NeoCandidate.id == db_session.selected_target)).first()
                    if cand:
                        ra = cand.ra_deg
                        dec = cand.dec_deg

        return ObservingSession(
            started_at=db_session.start_time,
            ended_at=db_session.end_time,
            notes=config.get("notes"),
            calibrations=calibrations,
            captures=stats.get("captures", []),
            selected_preset=config.get("selected_preset"),
            target_mode=db_session.target_mode,
            selected_target=db_session.selected_target,
            selected_target_ra=ra,
            selected_target_dec=dec,
            paused=(db_session.status == "paused"),
            associations=stats.get("associations", {}),
            master_calibrations=stats.get("master_calibrations", {}),
            predicted=stats.get("predicted", {}),
        )

    def set_window(self, start: str | None, end: str | None) -> None:
        with get_session() as session:
            # Update latest session or create a placeholder?
            # For now, update latest session if exists
            db_session = session.exec(
                select(DBObservingSession).order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                db_session.window_start = start
                db_session.window_end = end
                session.add(db_session)
                session.commit()
            else:
                # If no session exists, we can't persist without creating one.
                # But creating a session implies "started".
                # Maybe we just accept it's not persisted until first session?
                # Or we create a "config" session?
                # Let's just log it and move on for now.
                pass

    def log_event(self, message: str, level: str = "info") -> None:
        with get_session() as session:
            # Attach to active session if exists
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            event = SystemEvent(
                created_at=datetime.utcnow(),
                message=message,
                level=level,
                session_id=db_session.id if db_session else None
            )
            session.add(event)
            session.commit()

    def start(
        self,
        notes: str | None = None,
        calibration_filter: str | None = None,
        calibration_exposure_seconds: float | None = None,
    ) -> ObservingSession:
        plan = nightly_calibration_plan(filter_name=calibration_filter, exposure_seconds=calibration_exposure_seconds)
        cal_dicts = [
            {
                "type": item.type,
                "required": item.count,
                "completed": 0,
                "exposure_seconds": item.exposure_seconds,
                "filter": item.filter,
            }
            for item in plan
        ]
        
        with get_session() as session:
            # Check if there's already an active session?
            # Maybe end it?
            active = session.exec(
                select(DBObservingSession).where(DBObservingSession.status != "ended")
            ).first()
            if active:
                self.end("Restarting session")

            # Inherit window from previous session if available?
            last = session.exec(select(DBObservingSession).order_by(DBObservingSession.start_time.desc())).first()
            w_start = last.window_start if last else None
            w_end = last.window_end if last else None

            # Get selected preset from previous session or default?
            # We don't have a global selected_preset anymore, it's per session.
            # But we can look at the last one.
            sel_preset = last.config_snapshot.get("selected_preset") if last and last.config_snapshot else None

            new_session = DBObservingSession(
                start_time=datetime.utcnow(),
                status="active",
                window_start=w_start,
                window_end=w_end,
                config_snapshot={
                    "notes": notes,
                    "selected_preset": sel_preset
                },
                stats={
                    "calibrations": cal_dicts,
                    "captures": [],
                    "associations": {},
                    "master_calibrations": {},
                    "predicted": {}
                }
            )
            session.add(new_session)
            session.commit()
            session.refresh(new_session)
            
            self.log_event(f"Session started: {notes or 'No notes'}", "good")
            return self._to_view(new_session)

    def end(self, reason: str | None = None) -> ObservingSession | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                return None
                
            db_session.end_time = datetime.utcnow()
            db_session.status = "ended"
            session.add(db_session)
            session.commit()
            
            msg = f"Session ended: {reason}" if reason else "Session ended"
            self.log_event(msg, "warn")
            
            return self._to_view(db_session)

    def request_stop_auto_restart(self) -> None:
        self._stop_auto_restart = True

    def clear_stop_auto_restart(self) -> None:
        self._stop_auto_restart = False

    def stop_auto_restart_requested(self) -> bool:
        return self._stop_auto_restart

    def record_calibration(self, cal_type: str, count: int = 1) -> ObservingSession | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                return None
            
            # Update JSON stats
            stats = dict(db_session.stats) # Copy
            cals = stats.get("calibrations", [])
            for cal in cals:
                if cal["type"] == cal_type:
                    cal["completed"] = min(cal["required"], cal["completed"] + count)
                    break
            stats["calibrations"] = cals
            db_session.stats = stats
            
            session.add(db_session)
            session.commit()
            return self._to_view(db_session)

    def reset_calibrations(self, cal_type: str | None = None) -> ObservingSession | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                return None
            
            stats = dict(db_session.stats)
            cals = stats.get("calibrations", [])
            for cal in cals:
                if cal_type and cal["type"] != cal_type:
                    continue
                cal["completed"] = 0
            stats["calibrations"] = cals
            db_session.stats = stats
            
            session.add(db_session)
            session.commit()
            return self._to_view(db_session)

    def run_calibrations(self) -> dict:
        """Execute remaining calibrations via the bridge."""
        return run_calibration_plan(self)

    def add_capture(self, entry: dict) -> None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                # Start implicit session?
                # self.start() # recursive call might be tricky with context managers? 
                # No, start() uses its own with get_session().
                # But we are inside add_capture.
                # Let's call start() then re-query.
                self.start()
                db_session = session.exec(
                    select(DBObservingSession)
                    .where(DBObservingSession.status != "ended")
                    .order_by(DBObservingSession.start_time.desc())
                ).first()

            if db_session:
                stats = dict(db_session.stats)
                captures = stats.get("captures", [])
                captures.append(entry)
                stats["captures"] = captures
                db_session.stats = stats
                session.add(db_session)
                session.commit()

        try:
            record_capture(entry)
        except Exception:
            pass
        self.log_event(f"Captured {entry.get('target', 'unknown')} ({entry.get('kind', 'frame')})", "info")
        
        # Trigger post-processing
        from app.services.task_queue import TASK_QUEUE, Task
        TASK_QUEUE.submit(Task(
            name=f"process_capture_{entry.get('path')}",
            func=lambda: self._process_capture(entry)
        ))

    def add_captures(self, entries: List[dict]) -> None:
        for entry in entries:
            self.add_capture(entry)

    @property
    def selected_preset(self) -> dict[str, Any] | None:
        # Helper to get current preset without querying full session view
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            if db_session and db_session.config_snapshot:
                return db_session.config_snapshot.get("selected_preset")
            return None

    def select_preset(self, preset: ExposurePreset) -> dict[str, Any]:
        snapshot = _preset_to_snapshot(preset)
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                config = dict(db_session.config_snapshot or {})
                config["selected_preset"] = snapshot
                db_session.config_snapshot = config
                session.add(db_session)
                session.commit()
            else:
                # If no session, we can't store it?
                # Or we start one?
                # Or we just return it (it won't be persisted).
                # But user expects it to stick.
                # Let's just return it.
                pass
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
        current_preset = self.selected_preset
        if not current_preset:
            raise ValueError("no_preset_selected")
            
        snapshot = dict(current_preset)
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
        
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                config = dict(db_session.config_snapshot or {})
                config["selected_preset"] = snapshot
                db_session.config_snapshot = config
                session.add(db_session)
                session.commit()
                
        return snapshot

    @property
    def target_mode(self) -> str:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            return db_session.target_mode if db_session else "auto"

    @property
    def selected_target(self) -> str | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            return db_session.selected_target if db_session else None

    def set_target_mode(self, mode: str) -> None:
        mode = mode.lower()
        if mode not in {"auto", "manual"}:
            raise ValueError("invalid_mode")
            
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                db_session.target_mode = mode
                if mode == "auto":
                    db_session.selected_target = None
                session.add(db_session)
                session.commit()

    def select_target(self, trksub: str | None, mode: str = "manual") -> None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                if trksub:
                    db_session.selected_target = trksub
                    db_session.target_mode = mode
                else:
                    db_session.selected_target = None
                    # If clearing target, what mode? Keep current or reset to auto?
                    # Usually clearing means we are done or resetting.
                session.add(db_session)
                session.commit()

    def _process_capture(self, entry: dict) -> None:
        path = entry.get("path")
        if not path:
            return
            
        # 1. Solve
        from app.services.solver import solve_fits
        try:
            solve_fits(path)
        except Exception as e:
            self.log_event(f"Solving failed for {path}: {e}", "error")
            return

        # 2. Associate
        from app.services.analysis import AnalysisService
        from app.models import CaptureLog
        from astropy.wcs import WCS
        from pathlib import Path
        
        with get_session() as session:
            cap = session.exec(select(CaptureLog).where(CaptureLog.path == path)).first()
            if not cap:
                return
            
            wcs_path = Path(path).with_suffix(".wcs")
            if wcs_path.exists():
                try:
                    wcs = WCS(str(wcs_path))
                    analysis = AnalysisService(session)
                    assoc = analysis.auto_associate(session, cap, wcs)
                    if assoc:
                        self.log_event(f"Associated {cap.target} with candidate", "good")
                        self.set_association(path, assoc.ra_deg, assoc.dec_deg)
                    else:
                        self.log_event(f"Association failed for {cap.target}", "warn")
                except Exception as e:
                    self.log_event(f"Association error: {e}", "error")
            else:
                 self.log_event(f"No WCS for {cap.target}", "warn")

        # 3. Check for next target
        self._check_auto_pilot_progress()

    def _check_auto_pilot_progress(self) -> None:
        if self.target_mode != "auto":
            return

        if not self.current:
            return
            
        target = self.current.selected_target
        preset = self.current.selected_preset
        
        if not target or not preset:
            return
            
        expected_count = preset.get("count", 1)
        
        # Count associated captures for this target
        # We look at the session stats "captures" and "associations"
        # "captures" is a list of dicts
        # "associations" is a dict of path -> assoc_data
        
        captures = [c for c in self.current.captures if c.get("target") == target and c.get("kind") == "sequence"]
        
        if len(captures) < expected_count:
            # Not enough captures yet
            return
            
        # Check associations
        associated_count = 0
        for cap in captures:
            if cap.get("path") in self.current.associations:
                associated_count += 1
                
        if associated_count >= expected_count:
            self.log_event(f"Target {target} complete.", "good")

    def pause(self) -> ObservingSession | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                return None
                
            db_session.status = "paused"
            session.add(db_session)
            session.commit()
            self.log_event("Session paused", "warn")
            return self._to_view(db_session)

    def resume(self) -> ObservingSession | None:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status == "paused")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if not db_session:
                return None
                
            db_session.status = "active"
            session.add(db_session)
            session.commit()
            self.log_event("Session resumed", "good")
            return self._to_view(db_session)

    def set_association(self, path: str, ra_deg: float, dec_deg: float) -> dict[str, Any]:
        entry = {"ra_deg": ra_deg, "dec_deg": dec_deg}
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                stats = dict(db_session.stats)
                assocs = stats.get("associations", {})
                assocs[path] = entry
                stats["associations"] = assocs
                db_session.stats = stats
                session.add(db_session)
                session.commit()
        return entry

    def set_prediction(self, path: str, ra_deg: float, dec_deg: float) -> dict[str, Any]:
        entry = {"ra_deg": ra_deg, "dec_deg": dec_deg}
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                stats = dict(db_session.stats)
                preds = stats.get("predicted", {})
                preds[path] = entry
                stats["predicted"] = preds
                db_session.stats = stats
                session.add(db_session)
                session.commit()
        return entry

    @property
    def master_calibrations(self) -> dict[str, str]:
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            if db_session:
                return db_session.stats.get("master_calibrations", {})
            return {}

    def set_master(self, cal_type: str, path: str) -> dict[str, str]:
        cal_type = cal_type.lower()
        with get_session() as session:
            db_session = session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()
            
            if db_session:
                stats = dict(db_session.stats)
                masters = stats.get("master_calibrations", {})
                masters[cal_type] = path
                stats["master_calibrations"] = masters
                db_session.stats = stats
                session.add(db_session)
                session.commit()
                return masters
        return {cal_type: path} # Fallback return


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
