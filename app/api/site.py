"""Site configuration endpoints."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.api.deps import get_db
from app.models import SiteConfig

router = APIRouter(prefix="/site", tags=["site"])


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
