"""Capture log persistence helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from app.db.session import get_session
from app.models import AstrometricSolution, CaptureLog


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


def prune_missing_captures(session: Session | None = None) -> int:
    """Remove CaptureLog (and linked solutions) whose FITS files are missing."""
    def _prune(db: Session) -> int:
        stmt = select(CaptureLog)
        rows = db.exec(stmt).all()
        removed = 0
        for row in rows:
            if not row.path:
                continue
            if not Path(row.path).exists():
                # Delete associated solutions
                db.exec(AstrometricSolution.__table__.delete().where(AstrometricSolution.capture_id == row.id))
                db.exec(CaptureLog.__table__.delete().where(CaptureLog.id == row.id))
                removed += 1
        db.commit()
        return removed

    if session:
        return _prune(session)
    with get_session() as db:
        return _prune(db)


__all__ = ["record_capture", "prune_missing_captures"]
