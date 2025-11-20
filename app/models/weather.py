"""Weather snapshot models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class WeatherSnapshot(SQLModel, table=True):
    """Cached payloads fetched from remote weather providers."""

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(max_length=64, index=True)
    sensor_name: str = Field(max_length=128, index=True)
    endpoint: str = Field(max_length=512)
    fetched_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)
    temperature_c: float | None = None
    wind_speed_mps: float | None = None
    relative_humidity_pct: float | None = None
    precipitation_probability_pct: float | None = None
    precipitation_mm: float | None = None
    cloud_cover_pct: float | None = None
    payload: str = Field(description="Raw JSON payload returned by the provider")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)


__all__ = ["WeatherSnapshot"]
