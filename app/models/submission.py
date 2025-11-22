"""Submission log models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class SubmissionLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    channel: str = Field(max_length=32, description="email|api|sftp")
    status: str = Field(max_length=32, description="pending|sent|failed|acked")
    response: Optional[str] = Field(default=None, description="Raw response or error")
    report_path: Optional[str] = Field(default=None, description="Path to archived report payload")
    measurement_ids: Optional[str] = Field(default=None, description="JSON list of included measurement IDs")
    notes: Optional[str] = Field(default=None, max_length=255)


__all__ = ["SubmissionLog"]
