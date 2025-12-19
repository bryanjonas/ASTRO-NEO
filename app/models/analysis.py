"""Analysis and association models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class CandidateAssociation(SQLModel, table=True):
    """Association between a capture and a NEOCP candidate position.

    Can be auto-detected or manually corrected. Tracks quality metrics
    and residuals for validation.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    capture_id: int = Field(foreign_key="capturelog.id", index=True)
    ra_deg: float
    dec_deg: float

    # Quality metrics
    predicted_ra_deg: Optional[float] = None  # From ephemeris
    predicted_dec_deg: Optional[float] = None
    residual_arcsec: Optional[float] = None  # Distance from predicted
    snr: Optional[float] = None  # Signal-to-noise ratio
    peak_counts: Optional[float] = None  # Peak pixel value

    # Detection metadata
    method: str = Field(default="auto")  # "auto", "manual", "corrected"
    stars_subtracted: Optional[int] = None  # Number of catalog stars subtracted

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None


__all__ = ["CandidateAssociation"]
