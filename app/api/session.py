"""Session management API.

Database-backed session management using the observing_sessions table.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlmodel import select

from app.db.session import get_session_dep
from app.core.config import settings
from app.models.neocp import NeoCandidate, NeoEphemeris
from app.models.session import ObservingSession
from app.services.automation import AutomationService
from app.services.nina_client import NinaBridgeService
from app.services.whatsup import WhatsUpService

router = APIRouter(prefix="/session", tags=["session"])
logger = logging.getLogger(__name__)
_LAST_READY_SIGNATURE: tuple[str, ...] | None = None


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


@router.post("/start")
def start_session(
    request: SessionStartRequest,
    db: Session = Depends(get_session_dep)
) -> dict[str, Any]:
    """Start a new observing session.

    If manual_target_override is provided, uses that target.
    Otherwise, auto-selects the highest-ranked visible target from observability scores.
    """
    # Check if there's already an active session
    existing = db.exec(
        select(ObservingSession)
        .where(ObservingSession.status == "active")
    ).first()

    if existing:
        return {
            "success": False,
            "error": "An active session already exists. Stop it first.",
            "session_id": existing.id
        }

    # Determine target
    if request.manual_target_override:
        target_id = request.manual_target_override
        target_name = request.manual_target_override
    else:
        visible_targets, error = _get_whatsup_targets(db)
        if error:
            return {
                "success": False,
                "error": error,
            }

        best_target = visible_targets[0]
        target_id = best_target.id
        target_name = best_target.trksub

        logger.info(
            "Auto-selected target: %s (vmag=%s)",
            target_name,
            best_target.vmag,
        )

    # Create session record
    session = ObservingSession(
        start_time=datetime.utcnow(),
        status="active",
        target_mode="auto",
        selected_target=target_id,
        stats={}
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    logger.info(f"Created session {session.id} for target {target_name}")

    # Build target plan and execute
    try:
        automation = AutomationService(db_session=db)

        # Get candidate data for plan building (need RA/Dec/Vmag)
        candidate = db.exec(
            select(NeoCandidate)
            .where(NeoCandidate.id == target_id)
        ).first()

        if not candidate:
            session.status = "error"
            session.end_time = datetime.utcnow()
            db.commit()
            return {
                "success": False,
                "error": f"Target {target_id} not found in candidate table",
                "session_id": session.id
            }

        # Get observability for score
        target_dict = {
            "name": target_name,
            "candidate_id": target_id,
            "ra_deg": candidate.ra_deg or 0.0,
            "dec_deg": candidate.dec_deg or 0.0,
            "vmag": candidate.vmag,
            "score": 0.0,
        }

        plan = automation.build_target_plan(target_dict)

        logger.info(
            f"Executing plan for {plan.name}: {plan.count}x{plan.exposure_seconds}s "
            f"@ {plan.filter_name}, binning {plan.binning}"
        )

        # Execute the plan (this runs synchronously)
        result = automation.execute_target_plan(plan, session_id=session.id)

        # Update session stats
        session.stats = {
            "total_attempts": result["total_attempts"],
            "successful_captures": result["successful_captures"],
            "successful_associations": result["successful_associations"],
            "started_at": result["started_at"],
            "completed_at": result["completed_at"]
        }
        db.refresh(session)
        if session.status == "stopped":
            logger.info("Session %s stopped by user request", session.id)
            if not session.end_time:
                session.end_time = datetime.utcnow()
            db.commit()
            return {
                "success": False,
                "stopped": True,
                "session_id": session.id,
                "target_name": target_name,
                "result": result
            }
        session.status = "completed"
        session.end_time = datetime.utcnow()
        db.commit()

        return {
            "success": True,
            "session_id": session.id,
            "target_name": target_name,
            "result": result
        }

    except Exception as e:
        logger.error(f"Session execution failed: {e}", exc_info=True)
        session.status = "error"
        session.end_time = datetime.utcnow()
        session.stats = {"error": str(e)}
        db.commit()

        return {
            "success": False,
            "error": str(e),
            "session_id": session.id
        }


@router.post("/stop")
def stop_session(db: Session = Depends(get_session_dep)) -> dict[str, Any]:
    """Stop the currently active session."""
    session = db.exec(
        select(ObservingSession)
        .where(ObservingSession.status == "active")
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="No active session found")

    session.status = "stopped"
    session.end_time = datetime.utcnow()
    db.commit()

    try:
        nina = NinaBridgeService()
        nina.stop_sequence()
        nina.stop_guiding()
    except Exception as exc:
        logger.warning("Failed to stop NINA sequence/guide: %s", exc)

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
        .where(ObservingSession.status == "active")
        .order_by(ObservingSession.start_time.desc())
    ).first()

    if not session:
        return SessionStatusResponse(active=False)

    stats = session.stats or {}

    return SessionStatusResponse(
        active=True,
        session_id=session.id,
        target_name=session.selected_target,
        started_at=session.start_time.isoformat() if session.start_time else None,
        status=session.status,
        total_captures=stats.get("total_attempts", 0),
        successful_captures=stats.get("successful_captures", 0),
        successful_associations=stats.get("successful_associations", 0)
    )


__all__ = ["router"]
