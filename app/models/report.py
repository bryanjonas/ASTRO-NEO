"""Reporting-related models (measurements)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Measurement(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    capture_id: Optional[int] = Field(default=None, foreign_key="capturelog.id", index=True)
    target: str = Field(max_length=128, index=True)
    obs_time: datetime = Field(index=True)
    ra_deg: float = Field(index=True)
    dec_deg: float = Field(index=True)
    ra_uncert_arcsec: Optional[float] = None
    dec_uncert_arcsec: Optional[float] = None
    magnitude: Optional[float] = None
    mag_sigma: Optional[float] = None
    band: Optional[str] = Field(default=None, max_length=8)
    exposure_seconds: Optional[float] = None
    tracking_mode: Optional[str] = Field(default=None, max_length=32)
    station_code: Optional[str] = Field(default=None, max_length=8)
    observer: Optional[str] = Field(default=None, max_length=64)
    software: Optional[str] = Field(default=None, max_length=64)
    flags: Optional[str] = Field(default=None, description="JSON list of validation flags")
    reviewed: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


__all__ = ["Measurement"]
