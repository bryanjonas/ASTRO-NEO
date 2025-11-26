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
    horizon_mask_json: Optional[str] = Field(
        default=None, description="JSON horizon mask points (az_deg/alt_deg list)."
    )
    weather_sensors: Optional[str] = None
    equipment_profile: Optional[str] = Field(
        default=None,
        description="JSON blob describing active equipment capabilities (camera, focuser, mount).",
    )
    # ADES required fields
    telescope_design: str = Field(default="Reflector", description="Telescope design (e.g. Reflector, Refractor)")
    telescope_aperture: float = Field(default=0.0, description="Aperture in meters")
    telescope_detector: str = Field(default="CCD", description="Detector type (e.g. CCD, CMOS)")
