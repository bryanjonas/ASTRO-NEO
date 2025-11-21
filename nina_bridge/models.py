"""Pydantic models used by the bridge API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SlewCommand(BaseModel):
    ra_deg: float = Field(..., ge=0.0, lt=360.0)
    dec_deg: float = Field(..., ge=-90.0, le=90.0)


class ExposureRequest(BaseModel):
    filter: str = Field(..., min_length=1, max_length=16)
    binning: int = Field(1, ge=1, le=4)
    exposure_seconds: float | None = Field(default=None, gt=0)


class SequenceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    count: int = Field(..., ge=1, le=999)
    filter: str = Field(..., min_length=1, max_length=16)
    binning: int = Field(1, ge=1, le=4)
    exposure_seconds: float | None = Field(default=None, gt=0)
    tracking_mode: str | None = Field(default=None, max_length=32)
    focus_offset: float | None = Field(default=None)


class OverrideUpdate(BaseModel):
    manual_override: bool


class BridgeStatus(BaseModel):
    manual_override: bool
    dome_closed: bool
    weather: dict | None = None
    nina_status: dict | None = None
    equipment_profile: dict | None = None
    blockers: list[dict[str, Any]] = Field(default_factory=list)
    ready: dict[str, bool] | None = None


class DomeUpdate(BaseModel):
    closed: bool


class SequencePlanRequest(BaseModel):
    vmag: float | None = Field(default=None, ge=0.0)
    urgency: float | None = Field(default=None, ge=0.0, le=1.0)
    profile: str | None = Field(default=None, max_length=64)
    rate_arcsec_per_min: float | None = Field(default=None, ge=0.0)


class SequencePlanResponse(BaseModel):
    name: str
    filter: str
    binning: int
    count: int
    exposure_seconds: float
    tracking_mode: str
    focus_offset: float | None = None
    gain: int | None = None
    offset: int | None = None
    preset: str | None = None


class ConnectionRequest(BaseModel):
    connect: bool


class ParkToggle(BaseModel):
    park: bool


class FocuserMove(BaseModel):
    position: int = Field(..., ge=0, le=100000)
    speed: int | None = Field(default=None, ge=1, le=10)
