"""In-memory notification log for dashboard alerts."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, Iterable


@dataclass
class Notification:
    level: str
    message: str
    created_at: datetime
    context: dict | None = None


class NotificationLog:
    """Simple capped log for surfacing alerts to the dashboard."""

    def __init__(self, max_items: int = 50) -> None:
        self.max_items = max_items
        self._items: Deque[Notification] = deque(maxlen=max_items)

    def add(self, level: str, message: str, context: dict | None = None) -> Notification:
        note = Notification(level=level, message=message, created_at=datetime.utcnow(), context=context)
        self._items.appendleft(note)
        return note

    def recent(self, limit: int | None = None) -> Iterable[Notification]:
        if limit is None or limit >= len(self._items):
            return list(self._items)
        return list(self._items)[:limit]


NOTIFICATIONS = NotificationLog()

__all__ = ["Notification", "NotificationLog", "NOTIFICATIONS"]
