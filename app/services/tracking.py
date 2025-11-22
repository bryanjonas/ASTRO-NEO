"""Mount tracking helpers."""

from __future__ import annotations

import logging

import httpx

from app.core.config import settings
from app.services.notifications import NOTIFICATIONS

logger = logging.getLogger(__name__)


class TrackingService:
    """Set or verify mount tracking mode via the NINA bridge."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.nina_bridge_url).rstrip("/")
        self.timeout = timeout or settings.nina_bridge_timeout

    def set_tracking(self, mode: str = "sidereal") -> dict:
        url = f"{self.base_url}/telescope/tracking"
        payload = {"mode": mode}
        try:
            response = httpx.post(url, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json() if response.headers.get("content-type", "").startswith("application/json") else {"status": response.text}
        except httpx.HTTPError as exc:
            logger.warning("Failed to set tracking mode to %s: %s", mode, exc)
            NOTIFICATIONS.add("error", f"Tracking mode set failed ({mode})", {"error": str(exc)})
            raise

    def get_tracking(self) -> dict:
        url = f"{self.base_url}/telescope/tracking"
        try:
            response = httpx.get(url, timeout=self.timeout)
            response.raise_for_status()
            return response.json() if response.headers.get("content-type", "").startswith("application/json") else {"status": response.text}
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch tracking status: %s", exc)
            NOTIFICATIONS.add("error", "Tracking status fetch failed", {"error": str(exc)})
            raise


__all__ = ["TrackingService"]
