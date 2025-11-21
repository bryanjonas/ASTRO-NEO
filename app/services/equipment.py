"""Helpers for loading equipment profiles."""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session
from app.models import SiteConfig


class CameraCapabilities(BaseModel):
    type: str = "mono"
    filters: list[str] = Field(default_factory=list)
    max_binning: int = 2
    gain_presets: dict[str, int] = Field(default_factory=dict)
    offset_presets: dict[str, int] = Field(default_factory=dict)


class FocuserCapabilities(BaseModel):
    position_min: int = 0
    position_max: int = 100000


class MountCapabilities(BaseModel):
    supports_parking: bool = True


class EquipmentProfile(BaseModel):
    camera: CameraCapabilities
    focuser: FocuserCapabilities | None = None
    mount: MountCapabilities | None = None


def _load_profile(session: Session) -> EquipmentProfile | None:
    record = session.exec(
        select(SiteConfig).where(SiteConfig.name == settings.site_name)
    ).first()
    if not record or not record.equipment_profile:
        return None
    data = json.loads(record.equipment_profile)
    return EquipmentProfile.model_validate(data)


def get_active_equipment_profile(session: Session | None = None) -> EquipmentProfile | None:
    """Return the active equipment profile, if configured."""

    if session:
        return _load_profile(session)

    with get_session() as db:
        return _load_profile(db)


__all__ = [
    "EquipmentProfile",
    "CameraCapabilities",
    "FocuserCapabilities",
    "MountCapabilities",
    "get_active_equipment_profile",
]
