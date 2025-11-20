"""Site configuration model."""

from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class SiteConfig(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(default="default", index=True, unique=True)
    latitude: float
    longitude: float
    altitude_m: float
    bortle: Optional[int] = None
    horizon_mask_path: Optional[str] = None
    weather_sensors: Optional[str] = None
    equipment_profile: Optional[str] = Field(
        default=None,
        description="JSON blob describing active equipment capabilities (camera, focuser, mount).",
    )
