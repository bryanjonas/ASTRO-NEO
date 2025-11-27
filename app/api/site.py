"""Site configuration endpoints."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select, update

from app.api.deps import get_db
from app.models import SiteConfig

logger = logging.getLogger(__name__)

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


@router.post("/{site_id}/activate", response_model=SiteConfig)
def activate_site(site_id: int, session: Session = Depends(get_db)) -> SiteConfig:
    """Set a site as active, deactivating others."""
    site = session.get(SiteConfig, site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    
    # Deactivate all others
    session.exec(update(SiteConfig).values(is_active=False))
    
    site.is_active = True
    session.add(site)
    session.commit()
    session.refresh(site)
    
    # Trigger async horizon fetch for the newly active site
    try:
        from app.services.horizon import fetch_horizon_profile
        import asyncio
        asyncio.create_task(fetch_horizon_profile(site.latitude, site.longitude))
    except Exception:
        logger.error("Failed to trigger horizon fetch on activation", exc_info=True)
        
    return site


@router.post("/", response_model=SiteConfig)
async def upsert_site(config: SiteConfig, session: Session = Depends(get_db)) -> SiteConfig:
    """Create or update a site configuration."""
    # Auto-configure Open-Meteo if not present
    if not config.weather_sensors:
        import json
        config.weather_sensors = json.dumps([{"name": "Open-Meteo", "type": "open-meteo"}])

    existing = session.exec(select(SiteConfig).where(SiteConfig.name == config.name)).first()
    if existing:
        existing.latitude = config.latitude
        existing.longitude = config.longitude
        existing.altitude_m = config.altitude_m
        existing.bortle = config.bortle
        if config.weather_sensors:
            existing.weather_sensors = config.weather_sensors
        if config.equipment_profile:
            existing.equipment_profile = config.equipment_profile
            
        # If setting as active, deactivate others
        if config.is_active:
             session.exec(update(SiteConfig).where(SiteConfig.id != existing.id).values(is_active=False))
             
        session.add(existing)
        session.commit()
        session.refresh(existing)
        
        # Trigger async horizon fetch
        try:
            from app.services.horizon import fetch_horizon_profile
            profile = await fetch_horizon_profile(existing.latitude, existing.longitude)
            existing.horizon_mask_json = json.dumps(profile)
            session.add(existing)
            session.commit()
        except Exception:
            logger.error("Failed to auto-fetch horizon", exc_info=True)
        return existing

    # Create new
    # If this is the first site or requested active, ensure others are inactive
    if config.is_active or not session.exec(select(SiteConfig)).first():
        config.is_active = True
        session.exec(update(SiteConfig).values(is_active=False))
        
    session.add(config)
    session.commit()
    session.refresh(config)

    # Trigger async horizon fetch
    try:
        from app.services.horizon import fetch_horizon_profile
        profile = await fetch_horizon_profile(config.latitude, config.longitude)
        config.horizon_mask_json = json.dumps(profile)
        session.add(config)
        session.commit()
    except Exception:
        logger.error("Failed to auto-fetch horizon", exc_info=True)

    return config


@router.get("/{name}", response_model=SiteConfig)
def get_site(name: str, session: Session = Depends(get_db)) -> SiteConfig:
    site = session.exec(select(SiteConfig).where(SiteConfig.name == name)).first()
    if not site:
        raise HTTPException(status_code=404, detail="site_not_found")
    return site


@router.put("/{name}", response_model=SiteConfig)
async def update_site(name: str, payload: SiteConfigPayload, session: Session = Depends(get_db)) -> SiteConfig:
    """Update a site configuration."""
    import json
    
    # Auto-configure Open-Meteo if not present in payload or existing
    if not payload.weather_sensors:
         payload.weather_sensors = json.dumps([{"name": "Open-Meteo", "type": "open-meteo"}])

    record = session.exec(select(SiteConfig).where(SiteConfig.name == name)).first()
    if record:
        if payload.latitude is not None:
            record.latitude = payload.latitude
        if payload.longitude is not None:
            record.longitude = payload.longitude
        if payload.altitude_m is not None:
            record.altitude_m = payload.altitude_m
        if payload.bortle is not None:
            record.bortle = payload.bortle
        if payload.weather_sensors is not None:
            record.weather_sensors = payload.weather_sensors
        if payload.equipment_profile is not None:
            record.equipment_profile = payload.equipment_profile
            
        session.add(record)
        session.commit()
        session.refresh(record)
        
        # Trigger async horizon fetch
        try:
            from app.services.horizon import fetch_horizon_profile
            profile = await fetch_horizon_profile(record.latitude, record.longitude)
            record.horizon_mask_json = json.dumps(profile)
            session.add(record)
            session.commit()
        except Exception:
            logger.error("Failed to auto-fetch horizon", exc_info=True)
        
        return record
    
    raise HTTPException(status_code=404, detail="Site not found")


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
        logger.error("Failed to fetch horizon", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Failed to fetch horizon: {str(exc)}")
