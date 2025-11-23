"""Equipment profile persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class EquipmentProfileRecord(SQLModel, table=True):
    """Saved equipment profile definition."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=64, index=True, unique=True)
    payload_json: str = Field(description="JSON blob of equipment capabilities/presets")
    is_active: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)


__all__ = ["EquipmentProfileRecord"]
