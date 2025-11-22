"""Astrometry API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.models import AstrometricSolution
from app.services.astrometry import AstrometryService

router = APIRouter(prefix="/astrometry", tags=["astrometry"])


@router.post("/solve")
def solve(payload: dict[str, Any]) -> Any:
    capture_id = payload.get("capture_id")
    path = payload.get("path")
    if capture_id is None and path is None:
        raise HTTPException(status_code=400, detail="capture_id_or_path_required")
    svc = AstrometryService()
    result = svc.solve_capture(
        capture_id=capture_id,
        path=path,
        ra_hint=payload.get("ra_hint"),
        dec_hint=payload.get("dec_hint"),
        radius_deg=payload.get("radius_deg"),
        downsample=payload.get("downsample"),
    )
    return result


@router.get("/solutions")
def list_solutions(
    limit: int = 20,
    success: bool | None = None,
    session: Session = Depends(get_db),
) -> list[AstrometricSolution]:
    stmt = select(AstrometricSolution).order_by(AstrometricSolution.solved_at.desc()).limit(limit)
    if success is not None:
        stmt = stmt.where(AstrometricSolution.success == success)
    return session.exec(stmt).all()
