"""NEOCP candidate models."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class NeoCandidate(SQLModel, table=True):
    """Normalized entry scraped from the MPC NEO Confirmation Page."""

    id: Optional[str] = Field(default=None, primary_key=True)
    trksub: str = Field(index=True, unique=True, max_length=16)
    score: Optional[int] = Field(default=None, description="MPC ranking score (0-100)")
    observations: Optional[int] = Field(
        default=None, description="Number of observations reported in MPC table"
    )
    observed_ut: Optional[str] = Field(
        default=None, description="Observation timestamp string from MPC bracket text"
    )
    last_obs_utc: Optional[datetime] = Field(
        default=None, description="Parsed timestamp of most recent observation"
    )
    ra_deg: Optional[float] = Field(
        default=None, description="Right ascension (degrees, 0-360)"
    )
    dec_deg: Optional[float] = Field(default=None, description="Declination degrees")
    vmag: Optional[float] = Field(default=None, description="Apparent magnitude")
    status: Optional[str] = Field(
        default=None, description="Status text (Updated/Added) from MPC entry"
    )
    status_ut: Optional[str] = Field(
        default=None, description="Status timestamp (e.g., 'Nov. 16.77 UT')"
    )
    raw_entry: Optional[str] = Field(
        default=None, description="Raw MPC line text for trace/debugging"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class NeoCPSnapshot(SQLModel, table=True):
    """Raw HTML snapshot captured during each NEOCP poll."""

    __table_args__ = (UniqueConstraint("checksum", name="uq_neocp_snapshot_checksum"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    source_url: str = Field(max_length=512, description="URL used to fetch the snapshot")
    fetched_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)
    checksum: str = Field(
        max_length=64,
        index=True,
        unique=True,
        description="SHA-256 hash of the HTML payload for dedupe tracking",
    )
    html: str = Field(description="Raw HTML content from MPC")
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class NeoObservationPayload(SQLModel, table=True):
    """Raw payloads returned by the MPC get-obs-neocp endpoint."""

    __table_args__ = (
        UniqueConstraint(
            "trksub",
            "output_format",
            "checksum",
            name="uq_neocp_obs_trksub_format_checksum",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    trksub: str = Field(index=True, max_length=16)
    output_format: str = Field(max_length=16, description="Requested MPC output format")
    ades_version: str = Field(default="2022", max_length=8)
    payload_json: str = Field(description="JSON payload (stringified) returned by MPC")
    checksum: str = Field(
        max_length=64,
        index=True,
        description="SHA-256 hash of the payload for dedupe tracking",
    )
    fetched_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


class NeoEphemeris(SQLModel, table=True):
    """Cached ephemeris samples for each candidate/night.

    Supports both MPC and JPL Horizons sources.
    Horizons provides authoritative topocentric coordinates with
    light-time correction, aberration, and parallax.
    """

    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "epoch",
            name="uq_neoeph_candidate_epoch",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    candidate_id: str = Field(foreign_key="neocandidate.id", nullable=False, index=True)
    trksub: str = Field(max_length=16, index=True)
    epoch: datetime = Field(index=True)
    ra_deg: float
    dec_deg: float
    delta_au: Optional[float] = None
    r_au: Optional[float] = None
    rate_arcsec_per_min: Optional[float] = None
    position_angle_deg: Optional[float] = None
    magnitude: Optional[float] = None

    # Horizons-specific fields (motion rates)
    ra_rate_arcsec_min: Optional[float] = Field(
        default=None, description="RA rate including cos(dec) factor (arcsec/min)"
    )
    dec_rate_arcsec_min: Optional[float] = Field(
        default=None, description="Dec rate (arcsec/min)"
    )

    # Observing geometry (from Horizons)
    azimuth_deg: Optional[float] = Field(default=None, description="Azimuth (0=N, 90=E)")
    elevation_deg: Optional[float] = Field(default=None, description="Elevation above horizon")
    airmass: Optional[float] = Field(default=None, description="Relative optical airmass")
    solar_elongation_deg: Optional[float] = Field(
        default=None, description="Solar elongation angle"
    )
    lunar_elongation_deg: Optional[float] = Field(
        default=None, description="Lunar elongation angle"
    )

    # Predicted magnitude and uncertainty (from Horizons)
    v_mag_predicted: Optional[float] = Field(
        default=None, description="Predicted V magnitude from Horizons"
    )
    uncertainty_3sigma_arcsec: Optional[float] = Field(
        default=None, description="3-sigma positional uncertainty (arcsec)"
    )

    # Source tracking
    source: str = Field(default="MPC", max_length=16, description="Ephemeris source: MPC or HORIZONS")

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)


class NeoObservabilityBase(SQLModel):
    candidate_id: str = Field(foreign_key="neocandidate.id", nullable=False, index=True)
    trksub: str = Field(max_length=16, index=True)
    night_key: date = Field(index=True, description="UTC date the plan covers")
    night_start: datetime
    night_end: datetime
    window_start: datetime | None = None
    window_end: datetime | None = None
    duration_minutes: float | None = Field(default=None, description="Best window duration (minutes)")
    max_altitude_deg: float | None = None
    min_moon_separation_deg: float | None = None
    max_sun_altitude_deg: float | None = None
    score: float = 0.0
    score_breakdown: str | None = Field(
        default=None, description="JSON-encoded scoring components"
    )
    composite_score: float | None = Field(
        default=None, description="Multi-factor composite score (0-100) for dynamic prioritization"
    )
    peak_altitude_deg: float | None = Field(
        default=None, description="Peak altitude during observable window"
    )
    is_observable: bool = Field(default=False, description="True when window meets thresholds")
    limiting_factors: str | None = Field(
        default=None, description="JSON-encoded list of limiting factors"
    )
    computed_at: datetime = Field(default_factory=datetime.utcnow, nullable=False, index=True)


class NeoObservability(NeoObservabilityBase, table=True):
    """Per-candidate observability summary over the next planning horizon."""

    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "night_key",
            name="uq_neocandidate_observability_night",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)


class NeoObservabilityRead(NeoObservabilityBase):
    id: int


__all__ = [
    "NeoCandidate",
    "NeoCPSnapshot",
    "NeoObservationPayload",
    "NeoEphemeris",
    "NeoObservability",
    "NeoObservabilityRead",
]
