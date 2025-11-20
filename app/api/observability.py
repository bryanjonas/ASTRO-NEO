"""Observability API endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.api.deps import get_db
from app.models import NeoObservability, NeoObservabilityRead
from app.services.observability import ObservabilityService

router = APIRouter(prefix="/observability", tags=["observability"])


@router.get("/", response_model=List[NeoObservabilityRead])
def list_observability(session: Session = Depends(get_db)) -> list[NeoObservability]:
    stmt = select(NeoObservability).order_by(NeoObservability.score.desc())
    return session.exec(stmt).all()


@router.post("/refresh", response_model=List[NeoObservabilityRead])
def refresh_observability(
    trksubs: list[str] | None = Query(default=None, description="Optional list of trksubs to refresh"),
    session: Session = Depends(get_db),
) -> list[NeoObservability]:
    service = ObservabilityService(session=session)
    return service.refresh(trksubs=trksubs)
