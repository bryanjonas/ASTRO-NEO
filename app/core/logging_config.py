"""Shared logging configuration for all services."""

from __future__ import annotations

import logging
import os
from typing import Optional

from pythonjsonlogger import jsonlogger

_CONFIGURED = False


class _ServiceNameFilter(logging.Filter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        record.service = self.service_name
        return True


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
    root.setLevel(log_level)
    logging.captureWarnings(True)
    _CONFIGURED = True


__all__ = ["setup_logging"]
