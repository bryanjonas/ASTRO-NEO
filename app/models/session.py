from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON

class ObservingSession(SQLModel, table=True):
    __tablename__ = "observing_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    start_time: datetime = Field(default_factory=datetime.utcnow)
    end_time: Optional[datetime] = None
    status: str = Field(default="active")  # active, paused, ended
    target_mode: str = Field(default="auto")
    selected_target: Optional[str] = None
    
    # Persisted window settings
    window_start: Optional[str] = None
    window_end: Optional[str] = None

    # JSON fields for flexible storage
    config_snapshot: Dict[str, Any] = Field(default={}, sa_column=Column(JSON))
    stats: Dict[str, Any] = Field(default={}, sa_column=Column(JSON))
    
    # Relationships
    events: List["SystemEvent"] = Relationship(back_populates="session")


class SystemEvent(SQLModel, table=True):
    __tablename__ = "system_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    level: str = Field(default="info")
    message: str
    
    session_id: Optional[int] = Field(default=None, foreign_key="observing_sessions.id")
    session: Optional[ObservingSession] = Relationship(back_populates="events")
