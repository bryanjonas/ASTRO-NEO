"""In-memory state container for the mock NINA service."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from .config import settings


class TelescopeState(BaseModel):
    ra_deg: float = 0.0
    dec_deg: float = 0.0
    is_slewing: bool = False
    is_connected: bool = True
    is_parked: bool = False


class CameraState(BaseModel):
    is_exposing: bool = False
    last_status: str = "idle"
    last_exposure_start: Optional[datetime] = None
    last_exposure_duration: Optional[float] = None
    last_image_path: Optional[Path] = None


class SequenceState(BaseModel):
    is_running: bool = False
    current_index: int = 0
    total: int = 0
    name: Optional[str] = None


class FocuserState(BaseModel):
    is_moving: bool = False
    position: int = 50000


class ObservatoryState:
    """Singleton-like state object shared across requests."""

    def __init__(self) -> None:
        self.telescope = TelescopeState()
        self.camera = CameraState()
        self.sequence = SequenceState()
        self.focuser = FocuserState()
        self._lock = asyncio.Lock()
        self._image_counter = 0

    async def next_image_path(self) -> Path:
        async with self._lock:
            self._image_counter += 1
            idx = self._image_counter
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"IMG_{timestamp}_{idx:04d}.fits"
        return settings.data_dir / filename


STATE = ObservatoryState()

__all__ = [
    "STATE",
    "ObservatoryState",
    "TelescopeState",
    "CameraState",
    "SequenceState",
]
