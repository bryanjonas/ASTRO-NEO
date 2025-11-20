"""API dependencies."""

from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session

from app.db.session import get_session


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session
