"""Session management API.

Database-backed session management using the observing_sessions table.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlmodel import select

from app.db.session import get_session, get_session_dep
from app.core.config import settings
from app.models.neocp import NeoCandidate, NeoEphemeris
from app.models.session import ObservingSession
from app.services.automation import (
    AutomationService,
    clear_stop,
    is_stop_requested,
    request_stop,
)
from app.services.nina_client import NinaBridgeService
from app.services.observability import check_instant_visibility
from app.services.weather import WeatherService
from app.services.whatsup import WhatsUpService, maybe_auto_refresh

router = APIRouter(prefix="/session", tags=["session"])
logger = logging.getLogger(__name__)
_LAST_READY_SIGNATURE: tuple[str, ...] | None = None
# Set for the lifetime of a target chain (first target through the last).
# Closes the brief DB-query race between one target's ObservingSession row
# being marked completed/error and the next one being created, during which
# a query for status == "active" would otherwise find nothing.
_chain_active = threading.Event()

# Chain progress for dashboard display (see get_status). Lives only in this
# process's memory, same as _chain_active -- the chain itself doesn't
# survive a restart either, so there's nothing to persist. Written only by
# _run_target_chain's own thread; read by any request thread, so a reader
# might see a value from a moment ago, which is fine for a progress display.
_chain_progress: dict[str, Any] = {
    "auto_advance": False,
    "attempted_count": 0,
    "remaining_count": None,
}


class SessionStartRequest(BaseModel):
    """Request to start an observing session."""
    manual_target_override: str | None = Field(
        default=None,
        description="Optional target ID to observe. If null, auto-selects highest-ranked visible target"
    )


class SessionStatusResponse(BaseModel):
    """Response with current session status."""
    active: bool
    session_id: int | None = None
    target_name: str | None = None
    started_at: str | None = None
    status: str | None = None
    total_captures: int = 0
    successful_captures: int = 0
    successful_associations: int = 0
    # Auto-advance chain progress (see _chain_progress). chain_active can be
    # true even when `active` above is false, in the brief gap between one
    # target's session row completing and the next one being created.
    chain_active: bool = False
    chain_auto_advance: bool = False
    chain_attempted_count: int = 0
    chain_remaining_count: int | None = None


def _get_whatsup_targets(db: Session) -> tuple[list[NeoCandidate], str | None]:
    service = WhatsUpService(db)
    ranked = service.get_ranked_targets(limit=settings.whatsup_max_objects)
    if not ranked:
        return [], "No WhatsUp targets available. Use Refresh Targets first."
    return ranked, None


def _check_ephemeris_ready(db: Session, candidates: list[NeoCandidate]) -> list[str]:
    if not candidates:
        return []
    service = WhatsUpService(db)
    return service.missing_horizons(candidates)


@router.get("/ready")
def session_ready(db: Session = Depends(get_session_dep)) -> dict[str, Any]:
    global _LAST_READY_SIGNATURE
    visible_targets, error = _get_whatsup_targets(db)
    if error:
        return {"ready": False, "error": error}

    ranked = visible_targets
    top_targets = ranked[:5]
    if not top_targets:
        return {
            "ready": False,
            "error": "No targets available yet.",
        }
    missing_horizons = _check_ephemeris_ready(db, top_targets)
    if missing_horizons:
        return {
            "ready": False,
            "error": "Horizons positions missing for one or more targets",
            "missing_candidates": missing_horizons,
        }
    signature = tuple(sorted(item.id for item in top_targets))
    if _LAST_READY_SIGNATURE != signature:
        logger.info(
            "Start Session enabled (targets ready). Targets: %s",
            [
                {
                    "id": item.trksub or item.id,
                    "vmag": item.vmag,
                }
                for item in top_targets
            ],
        )
        _LAST_READY_SIGNATURE = signature
    return {"ready": True}


def _build_target_dict_from_candidate(db: Session, target_id: str) -> dict[str, Any] | None:
    candidate = db.exec(
        select(NeoCandidate).where(NeoCandidate.id == target_id)
    ).first()
    if not candidate:
        return None
    return {
        "name": target_id,
        "candidate_id": target_id,
        "ra_deg": candidate.ra_deg or 0.0,
        "dec_deg": candidate.dec_deg or 0.0,
        "vmag": candidate.vmag,
        "score": 0.0,
    }


def _latest_horizons_position(db: Session, candidate_id: str) -> tuple[float, float] | None:
    """Most recently cached Horizons position for a candidate.

    NeoCandidate.ra_deg/dec_deg are never populated for WhatsUp-sourced
    candidates (only neocp.py's older discovery flow sets those) -- the
    real, live position for these candidates only ever lands in
    NeoEphemeris via WhatsUpService.ensure_horizons_cache/
    refresh_targets_with_horizons. Read from there instead.
    """
    row = db.exec(
        select(NeoEphemeris)
        .where(NeoEphemeris.candidate_id == candidate_id)
        .where(NeoEphemeris.source == "HORIZONS")
        .order_by(NeoEphemeris.epoch.desc())
    ).first()
    if row is None or row.ra_deg is None or row.dec_deg is None:
        return None
    return row.ra_deg, row.dec_deg


def _first_available(
    db: Session, visible_targets: list[NeoCandidate], exclude_ids: set[str]
) -> tuple[NeoCandidate, float, float] | None:
    """Pick the best-ranked candidate not yet attempted this chain AND
    actually visible right now. A WhatsUp-cached "visible" status can be up
    to whatsup_refresh_minutes stale; re-verify locally (no network call --
    see check_instant_visibility) rather than trusting the snapshot. A
    candidate that fails this is skipped for now, not permanently excluded
    -- it isn't added to exclude_ids, so it can still be picked later in
    the same chain if it becomes visible again (e.g. rising in the east).

    Returns (candidate, ra_deg, dec_deg) so callers reuse the same cached
    position rather than re-querying it.
    """
    for candidate in visible_targets:
        if candidate.id in exclude_ids:
            continue
        position = _latest_horizons_position(db, candidate.id)
        if position is None:
            logger.debug("Skipping %s: no cached Horizons position available", candidate.id)
            continue
        ra_deg, dec_deg = position
        is_visible, reasons = check_instant_visibility(ra_deg, dec_deg)
        if not is_visible:
            logger.debug(
                "Skipping %s: not currently visible (%s)", candidate.id, "; ".join(reasons)
            )
            continue
        return candidate, ra_deg, dec_deg
    return None


def _resolve_next_target(
    db: Session, exclude_ids: set[str]
) -> tuple[dict[str, Any] | None, str | None, int]:
    """Pick the best-ranked visible target not already attempted this chain.

    If none are available (list empty, or everything in it already
    attempted), attempts one throttled auto-refresh before giving up --
    this is what lets session start and the auto-advance chain work without
    a manual "Refresh Targets" click, while a cooldown (see
    whatsup.maybe_auto_refresh) keeps repeated calls from hammering MPC.

    Returns (target_dict, error, remaining_count) -- remaining_count is how
    many *other* unattempted candidates are left after this one, for the
    dashboard's chain-progress display.
    """
    visible_targets, error = _get_whatsup_targets(db)
    found = _first_available(db, visible_targets, exclude_ids)

    if found is None and maybe_auto_refresh(db):
        visible_targets, error = _get_whatsup_targets(db)
        found = _first_available(db, visible_targets, exclude_ids)

    if found is None:
        return None, error or "No more unattempted visible targets", 0

    candidate, ra_deg, dec_deg = found
    available_count = sum(1 for c in visible_targets if c.id not in exclude_ids)

    return {
        "name": candidate.id,  # Use id (e.g. "16") not trksub (e.g. "(16)")
        "candidate_id": candidate.id,
        "ra_deg": ra_deg,
        "dec_deg": dec_deg,
        "vmag": candidate.vmag,
        "score": 0.0,
    }, None, max(0, available_count - 1)


def _weather_blocks_start(db: Session) -> str | None:
    """Return a reason string if weather is unsafe, else None (safe or unknown)."""
    weather_status = WeatherService(db).get_status()
    if weather_status is not None and not weather_status.is_safe:
        return f"Unsafe weather conditions: {', '.join(weather_status.reasons)}"
    return None


def _run_session_for_target(
    db: Session, target_dict: dict[str, Any]
) -> tuple[ObservingSession, dict[str, Any]]:
    """Create an ObservingSession row for one target and run its plan to completion."""
    target_id = target_dict["candidate_id"]
    target_name = target_dict["name"]

    session = ObservingSession(
        start_time=datetime.utcnow(),
        status="active",
        target_mode="auto",
        selected_target=target_id,
        stats={},
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    logger.info(f"Created session {session.id} for target {target_name}")

    automation = AutomationService(db_session=db)
    plan = automation.build_target_plan(target_dict)

    logger.info(
        f"Executing plan for {plan.name}: {plan.count}x{plan.exposure_seconds}s "
        f"@ {plan.filter_name}, binning {plan.binning}"
    )

    result = automation.execute_target_plan(plan, session_id=session.id)

    db.refresh(session)
    session.stats = {
        "total_attempts": result["total_attempts"],
        "successful_captures": result["successful_captures"],
        "successful_associations": result["successful_associations"],
        "started_at": result["started_at"],
        "completed_at": result["completed_at"],
    }
    if session.status in ("stopped", "stopping") or result.get("stopped"):
        logger.info("Session %s stopped by user request", session.id)
        session.status = "stopped"
        session.end_time = session.end_time or datetime.utcnow()
    elif result.get("aborted_reason"):
        logger.warning(
            "Session %s aborted early: %s", session.id, result["aborted_reason"]
        )
        session.status = "error"
        session.end_time = datetime.utcnow()
    else:
        session.status = "completed"
        session.end_time = datetime.utcnow()
    db.commit()
    return session, result


def _run_target_chain(first_target: dict[str, Any], auto_advance: bool) -> None:
    """Runs in a background thread: executes the first target, then -- if
    auto_advance -- keeps advancing to the next best not-yet-attempted
    visible target until the ranked list is exhausted, a stop is requested,
    weather turns unsafe, or too many targets in a row end in error (usually
    a sign something is genuinely broken, e.g. the mount is disconnected).
    """
    attempted: set[str] = set()
    target_dict: dict[str, Any] | None = first_target
    consecutive_target_failures = 0

    _chain_active.set()
    _chain_progress.update(
        auto_advance=auto_advance, attempted_count=0, remaining_count=None
    )
    try:
        while target_dict is not None:
            attempted.add(target_dict["candidate_id"])
            target_name = target_dict["name"]
            _chain_progress["attempted_count"] = len(attempted)

            try:
                with get_session() as db:
                    session, _result = _run_session_for_target(db, target_dict)
                    # Capture what we need while the session is still open --
                    # accessing an ORM attribute after the `with` block exits
                    # (session closed/expired) raises DetachedInstanceError.
                    session_status = session.status
            except Exception:
                logger.error(
                    "Unhandled error running session for %s; ending chain",
                    target_name,
                    exc_info=True,
                )
                break

            if session_status == "stopped":
                logger.info("Target chain stopped by user request after %s", target_name)
                break

            if session_status == "error":
                consecutive_target_failures += 1
            else:
                consecutive_target_failures = 0

            if not auto_advance:
                break

            if consecutive_target_failures >= settings.automation_max_consecutive_target_failures:
                logger.error(
                    "Ending target chain after %d consecutive target failures",
                    consecutive_target_failures,
                )
                break

            if is_stop_requested():
                logger.info("Target chain ending: stop requested")
                break

            with get_session() as db:
                weather_block = _weather_blocks_start(db)
                if weather_block:
                    logger.warning("Target chain ending: %s", weather_block)
                    break
                target_dict, reason, remaining_count = _resolve_next_target(db, attempted)
                _chain_progress["remaining_count"] = remaining_count

            if target_dict is None:
                logger.info("Target chain complete: %s", reason)
                break
    finally:
        _chain_active.clear()

    logger.info("Target chain finished.")


@router.post("/start")
def start_session(
    request: SessionStartRequest,
    db: Session = Depends(get_session_dep)
) -> dict[str, Any]:
    """Start a new observing session.

    If manual_target_override is provided, observes only that target.
    Otherwise, auto-selects the highest-ranked visible target and then
    automatically advances through the remaining ranked targets as each one
    finishes, until the list is exhausted, weather turns unsafe, too many
    targets in a row fail, or a stop is requested.

    Runs the actual capture chain in a background thread, so this call
    returns immediately -- poll /api/session/status for live progress.
    """
    # Check if there's already an active session
    existing = db.exec(
        select(ObservingSession)
        .where(ObservingSession.status == "active")
    ).first()

    if existing or _chain_active.is_set():
        return {
            "success": False,
            "error": "An active session already exists. Stop it first.",
            "session_id": existing.id if existing else None,
        }

    # Weather safety gate: refuse to slew/expose in unsafe conditions. If no
    # weather sensor is configured, or a fetch fails with no cached data,
    # weather_status is None and we fail open (unknown, not "unsafe") --
    # this matches prior behavior for sites with no weather monitoring set up.
    weather_block = _weather_blocks_start(db)
    if weather_block:
        logger.warning("Refusing to start session: %s", weather_block)
        return {"success": False, "error": weather_block}

    # Determine the first target
    if request.manual_target_override:
        target_dict = _build_target_dict_from_candidate(db, request.manual_target_override)
        if not target_dict:
            return {
                "success": False,
                "error": f"Target {request.manual_target_override} not found in candidate table",
            }
        auto_advance = False
    else:
        target_dict, error, _remaining_count = _resolve_next_target(db, exclude_ids=set())
        if error:
            return {"success": False, "error": error}
        auto_advance = True
        logger.info(
            "Auto-selected target: %s (vmag=%s)",
            target_dict["name"],
            target_dict["vmag"],
        )

    clear_stop()
    thread = threading.Thread(
        target=_run_target_chain,
        args=(target_dict, auto_advance),
        daemon=True,
        name="target-chain",
    )
    thread.start()

    return {
        "success": True,
        "target_name": target_dict["name"],
        "auto_advance": auto_advance,
    }


@router.post("/stop")
def stop_session(db: Session = Depends(get_session_dep)) -> dict[str, Any]:
    """Stop the currently active session."""
    session = db.exec(
        select(ObservingSession)
        .where(ObservingSession.status.in_(["active", "stopping"]))
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="No active session found")

    # Signal the capture loop to stop via in-memory event (avoids DB contention)
    request_stop()

    try:
        session.status = "stopping"
        db.commit()
    except Exception as exc:
        logger.warning("Could not set status to 'stopping' (will retry): %s", exc)
        db.rollback()

    nina = NinaBridgeService()
    try:
        logger.info(
            "Stop requested for session %s; waiting for any active exposure to finish.",
            session.id,
        )
        nina.wait_for_camera_idle(timeout=settings.nina_timeout)
    except Exception as exc:
        logger.warning("Timed out waiting for camera to go idle: %s", exc)

    try:
        nina.stop_sequence()
        nina.stop_guiding()
    except Exception as exc:
        logger.warning("Failed to stop NINA sequence/guide: %s", exc)

    try:
        db.refresh(session)
        session.status = "stopped"
        session.end_time = session.end_time or datetime.utcnow()
        db.commit()
    except Exception as exc:
        logger.warning("Could not finalize session stop: %s", exc)
        db.rollback()

    logger.info(f"Stopped session {session.id}")

    return {
        "success": True,
        "session_id": session.id,
        "message": "Session stopped"
    }


@router.get("/status")
def get_status(db: Session = Depends(get_session_dep)) -> SessionStatusResponse:
    """Get current session status."""
    session = db.exec(
        select(ObservingSession)
        .where(ObservingSession.status.in_(["active", "stopping"]))
        .order_by(ObservingSession.start_time.desc())
    ).first()

    chain_fields = dict(
        chain_active=_chain_active.is_set(),
        chain_auto_advance=_chain_progress["auto_advance"],
        chain_attempted_count=_chain_progress["attempted_count"],
        chain_remaining_count=_chain_progress["remaining_count"],
    )

    if not session:
        return SessionStatusResponse(active=False, **chain_fields)

    stats = session.stats or {}

    return SessionStatusResponse(
        active=True,
        session_id=session.id,
        target_name=session.selected_target,
        started_at=session.start_time.isoformat() if session.start_time else None,
        status=session.status,
        total_captures=stats.get("total_attempts", 0),
        successful_captures=stats.get("successful_captures", 0),
        successful_associations=stats.get("successful_associations", 0),
        **chain_fields,
    )


__all__ = ["router"]
