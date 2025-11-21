"""Dashboard bundle endpoints (HTMX/SSE-friendly)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.session import dashboard_status as session_dashboard_status
from app.services.imaging import retention_candidates
from app.services.notifications import NOTIFICATIONS

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


__all__ = ["router"]
