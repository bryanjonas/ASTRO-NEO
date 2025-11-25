"""Analysis and association models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class CandidateAssociation(SQLModel, table=True):
    """Manual association between a capture and a NEOCP candidate position."""

    id: Optional[int] = Field(default=None, primary_key=True)
    capture_id: int = Field(foreign_key="capturelog.id", index=True)
    ra_deg: float
    dec_deg: float
    created_at: datetime = Field(default_factory=datetime.utcnow)


__all__ = ["CandidateAssociation"]
