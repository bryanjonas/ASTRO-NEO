"""Equipment profile CRUD endpoints."""

from __future__ import annotations

from typing import Any, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_db
from app.models import EquipmentProfileRecord
from app.services.equipment import (
    EquipmentProfileSpec,
    activate_profile,
    list_profiles,
    save_profile,
)

router = APIRouter(prefix="/equipment", tags=["equipment"])


class EquipmentProfilePayload(BaseModel):
    name: str = Field(max_length=64)
    camera: dict
    focuser: dict | None = None
    mount: dict | None = None
    presets: list[dict] = Field(default_factory=list)
    activate: bool = Field(default=False)


@router.get("/profiles", response_model=List[EquipmentProfileRecord])
def get_profiles(session: Session = Depends(get_db)) -> Any:
    return list_profiles(session=session)


@router.post("/profiles", response_model=EquipmentProfileRecord)
def create_or_update_profile(payload: EquipmentProfilePayload, session: Session = Depends(get_db)) -> Any:
    spec = EquipmentProfileSpec(
        camera=payload.camera,
        focuser=payload.focuser,
        mount=payload.mount,
        presets=payload.presets,
    )
    return save_profile(
        name=payload.name,
        payload=spec,
        activate=payload.activate,
        session=session,
    )


@router.post("/profiles/{profile_id}/activate", response_model=EquipmentProfileRecord)
def activate_profile_endpoint(profile_id: int, session: Session = Depends(get_db)) -> Any:
    record = activate_profile(profile_id, session=session)
    if not record:
        raise HTTPException(status_code=404, detail="profile_not_found")
    return record


@router.delete("/profiles/{profile_id}")
def delete_profile(profile_id: int, session: Session = Depends(get_db)) -> Any:
    record = session.get(EquipmentProfileRecord, profile_id)
    if not record:
        raise HTTPException(status_code=404, detail="profile_not_found")
    session.delete(record)
    session.commit()
    return {"deleted": profile_id}


__all__ = ["router"]
