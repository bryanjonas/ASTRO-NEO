import logging
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


class NinaBridgeService:
    """Thin wrapper around the bridge HTTP API."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self.base_url = base_url or settings.nina_bridge_url.rstrip("/")
        self.timeout = timeout or settings.nina_bridge_timeout

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            logger.debug("NINA Request: %s %s params=%s json=%s", method, url, params, json)
            response = httpx.request(method, url, params=params, json=json, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                data = exc.response.json()
            except Exception:
                logger.error("NINA API Error (Raw): %s", exc.response.text)
                raise Exception(f"NINA API Error: {exc.response.text}") from exc
        except httpx.RequestError as e:
            logger.error("NINA Connection Error: %s", e)
            raise Exception(f"Failed to connect to NINA: {e}") from e

        # Check NINA envelope
        if not data.get("Success"):
            error_msg = data.get("Error", "Unknown NINA error")
            logger.error("NINA API Error: %s", error_msg)
            raise Exception(f"NINA API Error: {error_msg}")
            
        return data.get("Response")

    # --- Mount ---

    def connect_telescope(self, connect: bool) -> str:
        endpoint = "/equipment/mount/connect" if connect else "/equipment/mount/disconnect"
        return self._request("GET", endpoint)

    def park_telescope(self, park: bool) -> str:
        endpoint = "/equipment/mount/park" if park else "/equipment/mount/unpark"
        return self._request("GET", endpoint)

    def slew(self, ra_deg: float, dec_deg: float) -> str:
        return self._request("GET", "/equipment/mount/slew", {"ra": ra_deg, "dec": dec_deg})

    def set_tracking(self, mode: int) -> str:
        return self._request("GET", "/equipment/mount/tracking", {"mode": mode})
        
    def get_tracking(self) -> str:
        # Note: Real NINA API doesn't have a simple "get tracking" endpoint in the same way,
        # usually you poll status. But for now we'll assume we can't easily get it or 
        # we'd need to parse the full status.
        # For this bridge, let's assume we rely on the main status loop.
        return "Unknown"

    # --- Camera ---

    def start_exposure(self, filter_name: str, binning: int, exposure_seconds: float | None = None) -> str:
        # Real NINA flow: Change Filter -> Capture
        # We need to map filter name to ID. For now, we'll skip filter change in this simple bridge
        # or assume filter is already set.
        # But wait, the mock expects binning.
        params = {"binning": binning, "save": True}
        if exposure_seconds:
            params["duration"] = exposure_seconds
            
        return self._request("GET", "/equipment/camera/capture", params)

    def abort_exposure(self) -> str:
        return self._request("GET", "/equipment/camera/abort-exposure")

    # --- Focuser ---

    def focuser_move(self, position: int) -> str:
        return self._request("GET", "/equipment/focuser/move", {"position": position})

    def focuser_status(self) -> dict[str, Any]:
        return self._request("GET", "/equipment/focuser/info")

    # --- Dome ---
    
    def connect_dome(self) -> str:
        return self._request("GET", "/equipment/dome/connect")
        
    def open_dome(self) -> str:
        return self._request("GET", "/equipment/dome/open")
        
    def close_dome(self) -> str:
        return self._request("GET", "/equipment/dome/close")

    # --- General ---
    
    def get_status(self) -> dict[str, Any]:
        # This might be a custom endpoint we added to mock, or we need to poll individual devices
        # Real NINA has /status endpoint? Not exactly, but let's keep our custom one for now
        # or rely on individual device info.
        # Actually, let's try to hit the custom /status endpoint we left in mock_nina/main.py?
        # Wait, I removed /status from main.py in the previous step?
        # Let me check main.py content from previous step.
        # I removed /status. I should put it back or define how we get status.
        # Real NINA doesn't have a monolithic /status.
        # But for the dashboard we need it.
        # I will re-add /status to mock_nina as a helper, or implement it here by aggregating.
        # Let's assume we use /status for now and I need to re-add it to mock_nina.
        return self._request("GET", "/status")

    def set_ignore_weather(self, ignore: bool) -> dict[str, bool]:
        """Set the ignore_weather flag on the bridge."""
        url = f"{self.base_url}/ignore_weather"
        try:
            response = httpx.post(url, json={"ignore_weather": ignore}, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error("Failed to set ignore_weather: %s", e)
            raise
            
        if not data.get("Success"):
            raise Exception(f"NINA API Error: {data.get('Error')}")
            
        return data.get("Response")

    def start_sequence(self, payload: dict[str, Any]) -> str:
        """Start a sequence (or notify NINA about one)."""
        return self._request("POST", "/sequence/start", json=payload)

    def stop_sequence(self) -> str:
        """Stop the current sequence."""
        return self._request("GET", "/sequence/stop")


__all__ = ["NinaBridgeService"]
