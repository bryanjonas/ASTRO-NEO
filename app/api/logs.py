"""Log buffer endpoints for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.logging_config import get_log_buffer

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
def list_logs(limit: int = Query(100, ge=1, le=500)) -> dict[str, list[dict[str, str]]]:
    return {"logs": get_log_buffer(limit=limit)}


__all__ = ["router"]
