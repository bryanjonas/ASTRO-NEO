"""Pydantic models for mock NINA API schemas."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class SlewRequest(BaseModel):
    ra_deg: float = Field(..., description="Right ascension in degrees")
    dec_deg: float = Field(..., description="Declination in degrees")


class CameraExposureRequest(BaseModel):
    exposure_seconds: Optional[float] = None
    filter: str = Field(..., min_length=1, max_length=8)
    binning: int = 1
    filename: Optional[str] = Field(default=None, max_length=255)


class CameraStatusResponse(BaseModel):
    is_exposing: bool
    last_status: str
    last_exposure_start: Optional[datetime]
    last_exposure_duration: Optional[float]
    last_image_path: Optional[Path]


class SequenceStartRequest(BaseModel):
    name: str
    count: int = Field(..., gt=0)
    exposure_seconds: Optional[float] = None
    filter: str
    binning: int = 1


class SequenceStatusResponse(BaseModel):
    is_running: bool
    name: Optional[str]
    current_index: int
    total: int


class ConnectionToggle(BaseModel):
    connect: bool


class ParkRequest(BaseModel):
    park: bool


class FocuserMoveRequest(BaseModel):
    position: int = Field(..., ge=0, le=100000)
    speed: Optional[int] = Field(default=None, ge=1, le=10)


class StatusResponse(BaseModel):
    telescope: dict
    camera: dict
    sequence: dict
    focuser: dict


__all__ = [
    "SlewRequest",
    "CameraExposureRequest",
    "CameraStatusResponse",
    "SequenceStartRequest",
    "SequenceStatusResponse",
    "StatusResponse",
]
