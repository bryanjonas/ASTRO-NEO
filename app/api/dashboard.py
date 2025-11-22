"""Dashboard bundle endpoints (HTMX/SSE-friendly)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app.api.session import dashboard_status as session_dashboard_status
from app.services.imaging import retention_candidates
from app.services.notifications import NOTIFICATIONS
from app.api.deps import get_db
from app.models import AstrometricSolution

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/status")
def dashboard_status() -> Any:
    session_bundle = session_dashboard_status()
    expired = list(retention_candidates())
    notifications = [
        {
            "level": n.level,
            "message": n.message,
            "created_at": n.created_at.isoformat(),
            "context": n.context,
        }
        for n in NOTIFICATIONS.recent(limit=10)
    ]
    return {
        "bridge_blockers": session_bundle.get("bridge_blockers"),
        "bridge_ready": session_bundle.get("bridge_ready"),
        "bridge_status": session_bundle.get("bridge_status"),
        "session": session_bundle.get("session"),
        "retention": {"expired_count": len(expired)},
        "notifications": notifications,
    }


@router.get("/partials/captures")
def captures_partial() -> Any:
    from app.services.session import SESSION_STATE

    return SESSION_STATE.current.captures if SESSION_STATE.current else []


@router.get("/partials/solutions")
def solutions_partial(session: Session = Depends(get_db)) -> Any:
    stmt = select(AstrometricSolution).order_by(AstrometricSolution.solved_at.desc()).limit(15)
    rows = session.exec(stmt).all()
    return {
        "solutions": [
            {
                "id": row.id,
                "capture_id": row.capture_id,
                "measurement_id": getattr(row, "measurement_id", None),
                "path": row.path,
                "ra_deg": row.ra_deg,
                "dec_deg": row.dec_deg,
                "uncertainty_arcsec": row.uncertainty_arcsec,
                "snr": getattr(row, "snr", None),
                "mag_inst": getattr(row, "mag_inst", None),
                "flags": row.flags,
                "solved_at": row.solved_at,
                "success": row.success,
                "target": row.target,
            }
            for row in rows
        ]
    }


__all__ = ["router"]
