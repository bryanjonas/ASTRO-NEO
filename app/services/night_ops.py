"""Shared helpers for kicking off nightly automation runs."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import select

from app.db.session import get_session
from app.models import NeoCandidate, NeoObservability
from app.services.automation import AutomationService
from app.services.session import SESSION_STATE
from app.services.task_queue import TASK_QUEUE, Task

logger = logging.getLogger(__name__)

_sequence_lock = threading.Lock()
_sequence_running = False


def _is_sequence_running() -> bool:
    with _sequence_lock:
        return _sequence_running


def _set_sequence_running(value: bool) -> None:
    global _sequence_running
    with _sequence_lock:
        _sequence_running = value


@dataclass
class NightSessionError(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - human-friendly
        return self.message


def kickoff_imaging() -> dict[str, Any]:
    """Select the configured target (auto/manual) and start automation."""

    if _is_sequence_running():
        raise NightSessionError(status_code=409, message="An automation sequence is already running.")

    current_session = SESSION_STATE.current
    if current_session and current_session.paused:
        raise NightSessionError(status_code=409, message="Session is paused.")

    target = _choose_target()
    if not target:
        raise NightSessionError(status_code=404, message="No targets are currently observable (check time window, weather, or switch to Manual).")

    automation = AutomationService()
    targets_data = [
        {
            "name": target["trksub"],
            "ra_deg": target["ra_deg"],
            "dec_deg": target["dec_deg"],
            "vmag": target.get("vmag"),
            "candidate_id": target.get("candidate_id"),
        }
    ]
    plan = automation.build_sequential_target_plan(
        targets=targets_data,
        name=None,
        park_after=False,
    )

    mode = "manual" if SESSION_STATE.target_mode == "manual" else "auto"
    SESSION_STATE.select_target(target["trksub"], mode=mode)
    started_at = datetime.utcnow()

    def _run_sequence_task() -> None:
        exception_occurred = False
        try:
            automation.run_sequential_target_sequence(plan)
        except Exception as exc:  # pragma: no cover - background safety
            exception_occurred = True
            SESSION_STATE.log_event(
                f"Sequential target sequence failed for {target['trksub']}: {exc}",
                "error"
            )
            logger.error("Sequential target sequence for %s failed: %s", target["trksub"], exc, exc_info=True)
        finally:
            _set_sequence_running(False)

        if not exception_occurred and SESSION_STATE.target_mode == "auto":
            next_session = SESSION_STATE.current
            if not next_session or next_session.paused:
                return
            try:
                kickoff_imaging()
            except NightSessionError as exc:
                SESSION_STATE.log_event(f"Auto-pilot finished: {exc}", "warn")
            except Exception as exc:  # pragma: no cover - best-effort
                SESSION_STATE.log_event(f"Auto-pilot error restarting sequence: {exc}", "error")
                logger.error("Failed to restart auto sequence: %s", exc, exc_info=True)

    _set_sequence_running(True)
    TASK_QUEUE.submit(
        Task(
            name=f"sequential_target_sequence_{plan.name}",
            func=_run_sequence_task,
            retries=1,
            backoff_seconds=5.0,
        )
    )

    return {
        "sequence_name": plan.name,
        "targets": [target["trksub"]],
        "started_at": started_at.isoformat(),
    }


def _choose_target() -> dict[str, Any] | None:
    """Return the manually selected trksub or the highest-ranked target."""

    mode = SESSION_STATE.target_mode
    if mode == "manual":
        trksub = SESSION_STATE.selected_target
        if not trksub:
            raise NightSessionError(status_code=400, message="Manual mode selected but no target chosen.")
        target = _fetch_target(trksub)
        if not target:
            raise NightSessionError(status_code=404, message="Selected target is no longer visible.")
        return target
    return _fetch_target()


def _fetch_target(trksub: str | None = None) -> dict[str, Any] | None:
    """Fetch a target entry with coordinates suitable for automation."""
    return _fetch_target_internal(trksub)


def _fetch_target_internal(trksub: str | None = None, ignore_time: bool = False) -> dict[str, Any] | None:
    # Filter out already imaged targets if we are in auto mode (trksub is None)
    imaged_targets = set()
    if not trksub and SESSION_STATE.current:
        for cap in SESSION_STATE.current.captures:
            t = cap.get("target")
            if t:
                imaged_targets.add(t)

    with get_session() as session:
        stmt = (
            select(NeoObservability, NeoCandidate)
            .join(NeoCandidate, NeoCandidate.id == NeoObservability.candidate_id)
            .order_by(NeoObservability.score.desc())
        )
        if trksub:
            stmt = stmt.where(NeoObservability.trksub == trksub)
        else:
            stmt = stmt.where(NeoObservability.is_observable.is_(True))
            now = datetime.utcnow()
            # Always ensure the target is still viable (window hasn't ended)
            stmt = stmt.where(NeoObservability.window_end > now)

            if not ignore_time:
                stmt = stmt.where(NeoObservability.window_start <= now)
            
            # We can't easily filter by list in SQLModel/SQLAlchemy with a set if it's empty or large?
            # Actually .where(NeoObservability.trksub.not_in(imaged_targets)) works.
            if imaged_targets:
                stmt = stmt.where(NeoObservability.trksub.not_in(imaged_targets))
                
            stmt = stmt.limit(1)
        row = session.exec(stmt).first()
    if not row:
        return None
    obs, cand = row
    if cand.ra_deg is None or cand.dec_deg is None:
        raise NightSessionError(status_code=400, message="Target is missing coordinates (ra/dec).")
    return {
        "trksub": obs.trksub,
        "candidate_id": cand.id,
        "ra_deg": cand.ra_deg,
        "dec_deg": cand.dec_deg,
        "vmag": cand.vmag,
        "score": obs.score,
        "window_start": obs.window_start,
    }


__all__ = ["kickoff_imaging", "NightSessionError", "_fetch_target_internal"]
