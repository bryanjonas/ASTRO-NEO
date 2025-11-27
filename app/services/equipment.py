"""Helpers for loading and managing equipment profiles."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional, Sequence

from pydantic import BaseModel, Field
from sqlmodel import Session, select, update

from app.core.config import settings
from app.db.session import get_session
from app.models import EquipmentProfileRecord, SiteConfig


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


class TelescopeCapabilities(BaseModel):
    design: str = "Reflector"
    aperture: float = 0.28
    detector: str = "CCD"


class EquipmentProfileSpec(BaseModel):
    camera: CameraCapabilities
    focuser: FocuserCapabilities | None = None
    mount: MountCapabilities | None = None
    telescope: TelescopeCapabilities | None = None
    presets: list[dict] = Field(default_factory=list, description="Optional exposure presets")

# Backward-compatible alias for legacy imports
EquipmentProfile = EquipmentProfileSpec


def _load_profile(session: Session) -> EquipmentProfile | None:
    """Load active profile from DB, falling back to site-config JSON."""

    active_profile = session.exec(
        select(EquipmentProfileRecord).where(EquipmentProfileRecord.is_active.is_(True)).limit(1)
    ).first()
    if active_profile:
        return EquipmentProfileSpec.model_validate(json.loads(active_profile.payload_json))

    record = session.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
    if record and record.equipment_profile:
        return EquipmentProfileSpec.model_validate(json.loads(record.equipment_profile))
    return None


def get_active_equipment_profile(session: Session | None = None) -> EquipmentProfileSpec | None:
    """Return the active equipment profile, if configured."""

    if session:
        return _load_profile(session)

    with get_session() as db:
        return _load_profile(db)


def list_profiles(session: Session | None = None) -> Sequence[EquipmentProfileRecord]:
    """List saved profiles."""
    if session:
        return session.exec(
            select(EquipmentProfileRecord).order_by(EquipmentProfileRecord.updated_at.desc())
        ).all()
    with get_session() as db:
        return db.exec(
            select(EquipmentProfileRecord).order_by(EquipmentProfileRecord.updated_at.desc())
        ).all()


def save_profile(
    name: str, payload: EquipmentProfileSpec, activate: bool = False, session: Session | None = None
) -> EquipmentProfileRecord:
    """Create or update an equipment profile; optionally activate it."""

    def _save(db: Session) -> EquipmentProfileRecord:
        record = db.exec(
            select(EquipmentProfileRecord).where(EquipmentProfileRecord.name == name)
        ).first()
        payload_json = json.dumps(payload.model_dump())
        if record:
            record.payload_json = payload_json
            record.updated_at = datetime.utcnow()
        else:
            record = EquipmentProfileRecord(name=name, payload_json=payload_json)
        if activate:
            db.exec(update(EquipmentProfileRecord).values(is_active=False))
            record.is_active = True
        db.add(record)
        db.commit()
        db.refresh(record)
        # Mirror active profile into site config for compatibility
        if activate:
            site = db.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
            if site:
                site.equipment_profile = payload_json
                db.add(site)
                db.commit()
        return record

    if session:
        return _save(session)
    with get_session() as db:
        return _save(db)


def activate_profile(profile_id: int, session: Session | None = None) -> EquipmentProfileRecord | None:
    """Mark a profile active and mirror into site config."""

    def _activate(db: Session) -> EquipmentProfileRecord | None:
        record = db.get(EquipmentProfileRecord, profile_id)
        if not record:
            return None
        db.exec(update(EquipmentProfileRecord).values(is_active=False))
        record.is_active = True
        record.updated_at = datetime.utcnow()
        db.add(record)
        db.commit()
        db.refresh(record)
        site = db.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
        if site:
            site.equipment_profile = record.payload_json
            db.add(site)
            db.commit()
        return record

    if session:
        return _activate(session)
    with get_session() as db:
        return _activate(db)


def delete_profile(session: Session, profile_id: int) -> bool:
    """Delete an equipment profile if it's not active."""
    record = session.get(EquipmentProfileRecord, profile_id)
    if record and not record.is_active:
        session.delete(record)
        session.commit()
        return True
    return False


__all__ = [
    "EquipmentProfileSpec",
    "EquipmentProfile",
    "CameraCapabilities",
    "FocuserCapabilities",
    "MountCapabilities",
    "TelescopeCapabilities",
    "get_active_equipment_profile",
    "list_profiles",
    "save_profile",
    "activate_profile",
    "delete_profile",
]
