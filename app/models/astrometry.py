"""Astrometric solution models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class AstrometricSolution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    capture_id: Optional[int] = Field(default=None, foreign_key="capturelog.id", index=True)
    path: str = Field(max_length=512, index=True)
    ra_deg: Optional[float] = Field(default=None, index=True)
    dec_deg: Optional[float] = Field(default=None, index=True)
    orientation_deg: Optional[float] = None
    pixel_scale_arcsec: Optional[float] = None
    uncertainty_arcsec: Optional[float] = None
    success: bool = Field(default=False, index=True)
    solver_info: Optional[str] = Field(default=None, description="JSON blob of solver output")
    solved_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    duration_seconds: Optional[float] = None
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


__all__ = ["AstrometricSolution"]
