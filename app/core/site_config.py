"""Site configuration loader and bootstrap helpers."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session
from app.models import SiteConfig as SiteConfigModel

logger = logging.getLogger(__name__)


class HorizonMaskConfig(BaseModel):
    """Path and metadata for the stored horizon mask."""

    source: str
    resolution_deg: float | None = None


class WeatherSensorConfig(BaseModel):
    """Definition for connected weather sensors."""

    name: str
    type: str
    endpoint: str | None = None


class SiteFileConfig(BaseModel):
    """Site configuration representation loaded from config/site.yml."""

    name: str = "default"
    latitude: float
    longitude: float
    altitude_m: float
    bortle: int | None = None
    horizon_mask: HorizonMaskConfig | None = None
    weather_sensors: list[WeatherSensorConfig] = Field(default_factory=list)


def load_site_config(path: str | Path | None = None) -> SiteFileConfig:
    """Load the site configuration from YAML, seeding coordinates from .env when missing."""

    config_path = Path(path or settings.site_config_path)
    data: dict[str, Any] = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    site_payload = data.get("site", {})
    site_payload.setdefault("name", settings.site_name)
    site_payload.setdefault("latitude", settings.site_latitude)
    site_payload.setdefault("longitude", settings.site_longitude)
    site_payload.setdefault("altitude_m", settings.site_altitude_m)

    return SiteFileConfig.model_validate(site_payload)


def _site_config_to_model(site_config: SiteFileConfig) -> dict[str, Any]:
    payload = {
        "name": site_config.name,
        "latitude": site_config.latitude,
        "longitude": site_config.longitude,
        "altitude_m": site_config.altitude_m,
        "bortle": site_config.bortle,
        "horizon_mask_path": site_config.horizon_mask.source if site_config.horizon_mask else None,
        "weather_sensors": None,
    }
    if site_config.weather_sensors:
        payload["weather_sensors"] = json.dumps(
            [sensor.model_dump() for sensor in site_config.weather_sensors],
        )
    return payload


def sync_site_config_to_db(
    site_config: SiteFileConfig, session: Session | None = None
) -> SiteConfigModel:
    """Persist the site configuration into the SQL database."""

    payload = _site_config_to_model(site_config)

    def _sync(session: Session) -> SiteConfigModel:
        existing = session.exec(
            select(SiteConfigModel).where(SiteConfigModel.name == site_config.name)
        ).first()
        if existing:
            for field, value in payload.items():
                setattr(existing, field, value)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

        model = SiteConfigModel(**payload)
        session.add(model)
        session.commit()
        session.refresh(model)
        return model

    if session:
        return _sync(session)

    with get_session() as db_session:
        return _sync(db_session)


def bootstrap_site_config() -> None:
    """Ensure the configured site exists in the database."""

    try:
        site_config = load_site_config()
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to load site configuration: %s", exc)
        return

    try:
        sync_site_config_to_db(site_config)
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to persist site configuration: %s", exc)


__all__ = [
    "bootstrap_site_config",
    "load_site_config",
    "SiteFileConfig",
    "WeatherSensorConfig",
    "HorizonMaskConfig",
    "sync_site_config_to_db",
]
