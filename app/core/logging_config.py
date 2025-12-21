"""Shared logging configuration for all services."""

from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime
from typing import Optional

from pythonjsonlogger import jsonlogger

_CONFIGURED = False
_LOG_BUFFER: deque[dict[str, str]] = deque(maxlen=200)


class _ServiceNameFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        record.service = self.service_name
        return True


class _BufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - trivial
        try:
            timestamp = datetime.utcfromtimestamp(record.created).isoformat() + "Z"
            entry = {
                "time": timestamp,
                "level": record.levelname,
                "name": record.name,
                "message": record.getMessage(),
            }
            _LOG_BUFFER.appendleft(entry)
        except Exception:
            # Never break logging for buffer failures
            return


def setup_logging(service_name: Optional[str] = None) -> None:
    """Configure root logging with a JSON formatter and consistent metadata."""

    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    service = service_name or os.getenv("SERVICE_NAME", "astro-neo")

    handler = logging.StreamHandler()
    handler.setFormatter(
        jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s %(service)s")
    )
    handler.addFilter(_ServiceNameFilter(service))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.addHandler(_BufferHandler())
    root.setLevel(log_level)
    logging.captureWarnings(True)
    _CONFIGURED = True


def get_log_buffer(limit: int = 100) -> list[dict[str, str]]:
    return list(_LOG_BUFFER)[:limit]


__all__ = ["setup_logging", "get_log_buffer"]
