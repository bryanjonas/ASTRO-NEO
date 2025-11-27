"""Site configuration endpoints."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.api.deps import get_db
from app.models import SiteConfig

router = APIRouter(prefix="/site", tags=["site"])


class SiteConfigPayload(BaseModel):
    name: str = Field(default="default")
    latitude: float
    longitude: float
    altitude_m: float
    bortle: Optional[int] = None
    horizon_mask_path: Optional[str] = None
    horizon_mask_json: Optional[str] = None
    weather_sensors: Optional[str] = None  # JSON string or description
    equipment_profile: Optional[str] = Field(
        default=None,
        description="Optional JSON blob of the active equipment profile (mirrors selected profile).",
    )


@router.get("/", response_model=List[SiteConfig])
def list_sites(session: Session = Depends(get_db)) -> List[SiteConfig]:
    return session.exec(select(SiteConfig)).all()


@router.post("/", response_model=SiteConfig)
def upsert_site(config: SiteConfig, session: Session = Depends(get_db)) -> SiteConfig:
    existing = session.exec(select(SiteConfig).where(SiteConfig.name == config.name)).first()
    if existing:
        for field, value in config.model_dump(exclude_unset=True).items():
            setattr(existing, field, value)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    session.add(config)
    session.commit()
    session.refresh(config)
    return config


@router.get("/{name}", response_model=SiteConfig)
def get_site(name: str, session: Session = Depends(get_db)) -> SiteConfig:
    site = session.exec(select(SiteConfig).where(SiteConfig.name == name)).first()
    if not site:
        raise HTTPException(status_code=404, detail="site_not_found")
    return site


@router.put("/{name}", response_model=SiteConfig)
def update_site(name: str, payload: SiteConfigPayload, session: Session = Depends(get_db)) -> SiteConfig:
    """Update or create the site config."""
    record = session.exec(select(SiteConfig).where(SiteConfig.name == name)).first()
    if record:
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(record, field, value)
        session.add(record)
        session.commit()
        session.refresh(record)
        return record

    model = SiteConfig(**payload.model_dump())
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


@router.post("/{name}/horizon/refresh", response_model=SiteConfig)
async def refresh_horizon(name: str, session: Session = Depends(get_db)) -> SiteConfig:
    """Fetch and update horizon profile from PVGIS."""
    from app.services.horizon import fetch_horizon_profile
    import json

    site = session.exec(select(SiteConfig).where(SiteConfig.name == name)).first()
    if not site:
        raise HTTPException(status_code=404, detail="site_not_found")

    try:
        profile = await fetch_horizon_profile(site.latitude, site.longitude)
        # Convert list of dicts to JSON string for storage
        # The model expects a JSON string in horizon_mask_json
        # Format: [{"az": 0, "alt": 10}, ...]
        site.horizon_mask_json = json.dumps(profile)
        
        session.add(site)
        session.commit()
        session.refresh(site)
        return site
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch horizon: {str(exc)}")
