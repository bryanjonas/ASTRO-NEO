"""Client helpers for interacting with the NINA bridge service."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings


class NinaBridgeService:
    """Thin wrapper around the bridge HTTP API."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = base_url or settings.nina_bridge_url.rstrip("/")
        self.timeout = timeout or settings.nina_bridge_timeout

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = httpx.request(method, url, json=payload, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = None
            try:
                detail = exc.response.json()
            except Exception:  # pragma: no cover - best-effort parsing
                detail = exc.response.text
            raise HTTPException(status_code=exc.response.status_code, detail=detail)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"bridge_unreachable: {exc}") from exc

        if not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def status(self) -> Any:
        return self._request("GET", "/status")

    def equipment_profile(self) -> Any:
        return self._request("GET", "/equipment/profile")

    def set_override(self, manual_override: bool) -> Any:
        return self._request("POST", "/override", {"manual_override": manual_override})

    def set_dome(self, closed: bool) -> Any:
        return self._request("POST", "/dome", {"closed": closed})

    def connect_telescope(self, connect: bool) -> Any:
        return self._request("POST", "/telescope/connect", {"connect": connect})

    def park_telescope(self, park: bool) -> Any:
        return self._request("POST", "/telescope/park", {"park": park})

    def slew(self, ra_deg: float, dec_deg: float) -> Any:
        return self._request("POST", "/telescope/slew", {"ra_deg": ra_deg, "dec_deg": dec_deg})

    def focuser_move(self, position: int, speed: int | None = None) -> Any:
        payload: dict[str, Any] = {"position": position}
        if speed is not None:
            payload["speed"] = speed
        return self._request("POST", "/focuser/move", payload)

    def focuser_status(self) -> Any:
        return self._request("GET", "/focuser/status")

    def start_exposure(self, filter_name: str, binning: int, exposure_seconds: float | None = None) -> Any:
        payload: dict[str, Any] = {"filter": filter_name, "binning": binning}
        if exposure_seconds is not None:
            payload["exposure_seconds"] = exposure_seconds
        return self._request("POST", "/camera/exposure", payload)

    def plan_sequence(self, plan: dict[str, Any]) -> Any:
        return self._request("POST", "/sequence/plan", plan)

    def start_sequence(self, sequence: dict[str, Any]) -> Any:
        return self._request("POST", "/sequence/start", sequence)


__all__ = ["NinaBridgeService"]
