"""Capture listing endpoints for the minimal dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.db.session import get_session_dep
from app.models import AstrometricSolution, CaptureLog, CandidateAssociation
from app.models.session import ObservingSession

router = APIRouter(prefix="/captures", tags=["captures"])


@router.get("")
def list_captures(
    limit: int = Query(10, ge=1, le=100),
    session_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_session_dep),
) -> dict[str, Any]:
    stmt = select(CaptureLog)

    if session_id is not None:
        session = db.exec(
            select(ObservingSession).where(ObservingSession.id == session_id)
        ).first()
        if not session:
            return {"captures": []}
        start = session.start_time
        end = session.end_time or datetime.utcnow()
        stmt = stmt.where(CaptureLog.started_at >= start).where(CaptureLog.started_at <= end)

    captures = db.exec(
        stmt.order_by(CaptureLog.started_at.desc()).limit(limit)
    ).all()

    capture_ids = [c.id for c in captures if c.id]

    solutions = []
    associations = []
    if capture_ids:
        solutions = db.exec(
            select(AstrometricSolution)
            .where(AstrometricSolution.capture_id.in_(capture_ids))
            .order_by(AstrometricSolution.solved_at.desc())
        ).all()
        associations = db.exec(
            select(CandidateAssociation)
            .where(CandidateAssociation.capture_id.in_(capture_ids))
            .order_by(CandidateAssociation.created_at.desc())
        ).all()

    solution_map: dict[int, AstrometricSolution] = {}
    for row in solutions:
        if row.capture_id and row.capture_id not in solution_map:
            solution_map[row.capture_id] = row

    association_map: dict[int, CandidateAssociation] = {}
    for row in associations:
        if row.capture_id and row.capture_id not in association_map:
            association_map[row.capture_id] = row

    payload = []
    for cap in captures:
        assoc = association_map.get(cap.id or -1)
        solution = solution_map.get(cap.id or -1)
        payload.append(
            {
                "id": cap.id,
                "target": cap.target,
                "path": cap.path,
                "started_at": cap.started_at.isoformat() if cap.started_at else None,
                "has_wcs": bool(solution and solution.success),
                "association": {
                    "id": assoc.id,
                    "residual_arcsec": assoc.residual_arcsec,
                }
                if assoc
                else None,
            }
        )

    return {"captures": payload}


__all__ = ["router"]
