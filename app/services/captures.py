"""Capture log persistence helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session

from app.db.session import get_session
from app.models import CaptureLog


def record_capture(entry: dict[str, Any], session: Optional[Session] = None) -> None:
    """Persist a capture log entry."""
    model = CaptureLog(
        kind=entry.get("kind", "unknown"),
        target=entry.get("target") or "unknown",
        sequence=entry.get("sequence"),
        index=entry.get("index"),
        path=entry.get("path", ""),
        started_at=_parse_dt(entry.get("started_at")) or datetime.utcnow(),
    )

    def _save(db: Session) -> None:
        db.add(model)
        db.commit()

    if session:
        _save(session)
    else:
        with get_session() as db:
            _save(db)


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


__all__ = ["record_capture"]
