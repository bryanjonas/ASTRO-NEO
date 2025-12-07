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


class CameraProfileConfig(BaseModel):
    """Camera-specific capabilities."""

    type: str = "mono"
    filters: list[str] = Field(default_factory=list)
    max_binning: int = 2
    gain_presets: dict[str, int] = Field(default_factory=dict)
    offset_presets: dict[str, int] = Field(default_factory=dict)


class FocuserProfileConfig(BaseModel):
    """Focuser range and behavior."""

    position_min: int = 0
    position_max: int = 100000


class MountProfileConfig(BaseModel):
    """Mount capabilities."""

    supports_parking: bool = True


class EquipmentProfileConfig(BaseModel):
    """Aggregate equipment capabilities for the site."""

    camera: CameraProfileConfig
    focuser: FocuserProfileConfig | None = None
    mount: MountProfileConfig | None = None


class SiteFileConfig(BaseModel):
    """Site configuration representation loaded from config/site.yml."""

    name: str = "default"
    latitude: float
    longitude: float
    altitude_m: float
    bortle: int | None = None
    horizon_mask: HorizonMaskConfig | None = None
    is_active: bool = False
    weather_sensors: list[WeatherSensorConfig] = Field(default_factory=list)
    equipment_profile: EquipmentProfileConfig | None = None
    timezone: str = "UTC"


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
    if settings.site_bortle is not None:
        site_payload.setdefault("bortle", settings.site_bortle)
    
    # Default timezone if not in config
    site_payload.setdefault("timezone", "UTC")

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
        "equipment_profile": None,
        "timezone": site_config.timezone,
    }
    if site_config.weather_sensors:
        payload["weather_sensors"] = json.dumps(
            [sensor.model_dump() for sensor in site_config.weather_sensors],
        )
    if site_config.equipment_profile:
        payload["equipment_profile"] = json.dumps(site_config.equipment_profile.model_dump())
    return payload


def db_site_to_file_config(db_site: SiteConfigModel) -> SiteFileConfig:
    """Convert a database SiteConfig model to a SiteFileConfig object."""
    weather_sensors = []
    if db_site.weather_sensors:
        try:
            sensors_data = json.loads(db_site.weather_sensors)
            # Handle both list of dicts and list of strings (legacy/simple format)
            if isinstance(sensors_data, list):
                for s in sensors_data:
                    if isinstance(s, dict):
                        weather_sensors.append(WeatherSensorConfig(**s))
                    elif isinstance(s, str):
                        weather_sensors.append(WeatherSensorConfig(name=s, type="remote"))
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse weather_sensors from DB: %s", db_site.weather_sensors)

    equipment_profile = None
    if db_site.equipment_profile:
        try:
            eq_data = json.loads(db_site.equipment_profile)
            equipment_profile = EquipmentProfileConfig(**eq_data)
        except (json.JSONDecodeError, ValueError):
            pass

    horizon_mask = None
    if db_site.horizon_mask_path:
        horizon_mask = HorizonMaskConfig(source=db_site.horizon_mask_path)

    return SiteFileConfig(
        name=db_site.name,
        latitude=db_site.latitude,
        longitude=db_site.longitude,
        altitude_m=db_site.altitude_m,
        bortle=db_site.bortle,
        is_active=db_site.is_active,
        horizon_mask=horizon_mask,
        weather_sensors=weather_sensors,
        equipment_profile=equipment_profile,
        timezone=db_site.timezone,
    )


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
        with get_session() as session:
            # Check if any sites exist
            existing_count = session.exec(select(SiteConfigModel)).all()
            
            if not existing_count:
                logger.info("No sites found in DB. Seeding from environment/config.")
                try:
                    site_config = load_site_config()
                    # Create default site as active
                    db_site = sync_site_config_to_db(site_config, session=session)
                    db_site.is_active = True
                    session.add(db_site)
                    session.commit()
                    logger.info("Seeded default site '%s' as active.", db_site.name)
                except Exception as exc:
                    logger.error("Failed to load/seed site configuration: %s", exc)
                    return
            else:
                # Ensure at least one site is active
                active = session.exec(select(SiteConfigModel).where(SiteConfigModel.is_active == True)).first()
                if not active:
                    logger.warning("No active site found. Activating the first available site.")
                    first_site = session.exec(select(SiteConfigModel)).first()
                    if first_site:
                        first_site.is_active = True
                        session.add(first_site)
                        session.commit()
                        logger.info("Activated site '%s'.", first_site.name)

    except Exception as exc:  # pragma: no cover - defensive logging
        logger.error("Failed to bootstrap site configuration: %s", exc)


__all__ = [
    "bootstrap_site_config",
    "load_site_config",
    "SiteFileConfig",
    "WeatherSensorConfig",
    "HorizonMaskConfig",
    "EquipmentProfileConfig",
    "sync_site_config_to_db",
    "db_site_to_file_config",
]
