"""SQLModel session management."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings

engine = create_engine(settings.database_url, echo=False, pool_pre_ping=True)


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Iterator[Session]:
    """Get a database session as a context manager (for use with 'with' statement).

    This is decorated with @contextmanager for use in regular code with 'with' statement.
    For FastAPI dependency injection, use get_session_dep() instead.
    """
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


def get_session_dep() -> Iterator[Session]:
    """Get a database session for FastAPI dependency injection.

    This is a plain generator function (not decorated with @contextmanager)
    because FastAPI's Depends() handles the context management.

    Use this with FastAPI's Depends():
        def my_endpoint(db: Session = Depends(get_session_dep)):
            ...
    """
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
