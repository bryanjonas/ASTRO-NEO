"""Capture log model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class CaptureLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str = Field(max_length=32, index=True)
    target: str = Field(max_length=128, index=True)
    sequence: Optional[str] = Field(default=None, max_length=128, index=True)
    index: Optional[int] = Field(default=None, index=True)
    path: str = Field(max_length=512)
    started_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = ["CaptureLog"]
