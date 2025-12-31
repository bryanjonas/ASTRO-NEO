"""WhatsUp target listing endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.api.deps import get_db
from app.services.whatsup import WhatsUpService

router = APIRouter(prefix="/whatsup", tags=["whatsup"])


@router.get("/targets")
def list_whatsup_targets(
    limit: int = Query(10, ge=1, le=100),
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    service = WhatsUpService(session=session)
    targets = service.get_ranked_targets(limit=limit)
    payload: list[dict[str, Any]] = []
    for target in targets:
        payload.append(
            {
                "id": target.id,
                "trksub": target.trksub,
                "vmag": target.vmag,
                "updated_at": target.updated_at.isoformat() if target.updated_at else None,
            }
        )
    return payload


__all__ = ["router"]
