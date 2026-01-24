import logging
import time
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger(__name__)


class NinaBridgeService:
    """Direct NINA API client (bridge removed - now calls NINA directly)."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        # Call NINA directly - bridge service has been removed
        self.base_url = base_url or settings.nina_url
        self.timeout = timeout or settings.nina_timeout

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        try:
            logger.debug("NINA Request: %s %s params=%s json=%s", method, url, params, json)
            response = httpx.request(
                method,
                url,
                params=params,
                json=json,
                timeout=timeout or self.timeout,
            )
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

    def connect_telescope(self, connect: bool, device_id: str | None = None) -> str:
        endpoint = "/equipment/mount/connect" if connect else "/equipment/mount/disconnect"
        params = {}
        if connect and device_id:
            params["to"] = device_id
        return self._request("GET", endpoint, params=params)

    def list_telescopes(self) -> list[dict[str, Any]]:
        """List available telescope mounts."""
        data = self._request("GET", "/equipment/mount/list-devices")
        return data if isinstance(data, list) else []

    def park_telescope(self, park: bool) -> str:
        endpoint = "/equipment/mount/park" if park else "/equipment/mount/unpark"
        return self._request("GET", endpoint)

    def mount_info_raw(self) -> dict[str, Any]:
        return self._request("GET", "/equipment/mount/info")

    def mount_info(self) -> dict[str, Any]:
        raw = self.mount_info_raw()
        return self._normalize_telescope(raw)

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

    def list_cameras(self) -> list[dict[str, Any]]:
        """List available cameras."""
        data = self._request("GET", "/equipment/camera/list-devices")
        # NINA returns a list of devices
        return data if isinstance(data, list) else []

    def connect_camera(self, device_id: str | None = None) -> str:
        """Connect to a specific camera."""
        params = {}
        if device_id:
            params["to"] = device_id
        return self._request("GET", "/equipment/camera/connect", params)

    def start_exposure(
        self,
        filter_name: str,
        binning: int,
        exposure_seconds: float | None = None,
        target: str | None = None,
        request_solve: bool = True,
    ) -> Any:
        params: dict[str, Any] = {
            "binning": binning,
            "save": True,
            # Live NINA tests show reliable file saves with fire-and-forget capture.
            "solve": False,
            "waitForResult": False,
            "getResult": False,
            "omitImage": True,
        }
        if exposure_seconds:
            params["duration"] = exposure_seconds
        if target:
            params["targetName"] = target

        timeout = None
        if exposure_seconds and params["waitForResult"]:
            # For waitForResult=True, allow extra time for exposure and readout.
            timeout = max(self.timeout, float(exposure_seconds) + 30.0)

        return self._request("GET", "/equipment/camera/capture", params, timeout=timeout)

    def abort_exposure(self) -> str:
        return self._request("GET", "/equipment/camera/abort-exposure")

    def wait_for_mount_ready(
        self,
        timeout: float = 180.0,
        poll_interval: float = 1.0,
        settle_seconds: float = 3.0,
    ) -> None:
        """Wait for mount slewing to stop, then apply a fixed settle window."""
        deadline = time.time() + timeout
        settle_deadline = 0.0
        while time.time() < deadline:
            status = self._request("GET", "/equipment/mount/info")
            if status.get("Slewing", False):
                settle_deadline = 0.0
            else:
                if settle_deadline == 0.0:
                    settle_deadline = time.time() + settle_seconds
                elif time.time() >= settle_deadline:
                    return
            time.sleep(poll_interval)
        raise Exception("Mount is still slewing or settling after timeout")

    def wait_for_camera_idle(self, timeout: float = 120.0, poll_interval: float = 0.5) -> None:
        """Ensure the camera is not currently exposing before starting a new capture."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self._request("GET", "/equipment/camera/info")
            if not info.get("IsExposing", False):
                return
            time.sleep(poll_interval)
        raise Exception("Camera never reached idle state before exposure")

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
        telescope_raw = self._request("GET", "/equipment/mount/info")
        camera_raw = self._request("GET", "/equipment/camera/info")
        sequence_raw = self._try_request("GET", "/sequence/json")
        focuser_raw = self._try_request("GET", "/equipment/focuser/info")

        telescope = self._normalize_telescope(telescope_raw)
        camera = self._normalize_camera(camera_raw)
        sequence = self._normalize_sequence(sequence_raw)
        focuser = self._normalize_focuser(focuser_raw)

        nina_status = {
            "telescope": telescope,
            "camera": camera,
            "sequence": sequence,
            "focuser": focuser,
        }

        blockers = self._derive_blockers(telescope, camera, sequence)
        ready = {
            "ready_to_expose": self._ready_to_expose(telescope, camera, sequence),
        }

        return {
            "nina_status": nina_status,
            "ready": ready,
            "blockers": blockers,
        }

    def _try_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any | None:
        try:
            return self._request(method, path, params=params)
        except Exception as exc:
            logger.debug("Optional NINA endpoint unavailable (%s): %s", path, exc)
            return None

    @staticmethod
    def _normalize_telescope(raw: dict[str, Any]) -> dict[str, Any]:
        coords = raw.get("Coordinates") or {}
        ra_deg = coords.get("RADegrees")
        if ra_deg is None:
            ra_hours = raw.get("RightAscension")
            ra_deg = float(ra_hours) * 15.0 if ra_hours is not None else None
        dec_deg = coords.get("Dec")
        if dec_deg is None:
            dec_deg = raw.get("Declination")
        return {
            "is_connected": bool(raw.get("Connected", True)),
            "is_parked": bool(raw.get("AtPark", False)),
            "is_slewing": bool(raw.get("Slewing", False)),
            "ra_deg": float(ra_deg) if ra_deg is not None else None,
            "dec_deg": float(dec_deg) if dec_deg is not None else None,
        }

    @staticmethod
    def _normalize_camera(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "is_connected": bool(raw.get("Connected", True)),
            "is_exposing": bool(raw.get("IsExposing", False)),
            "temperature": raw.get("Temperature"),
        }

    @staticmethod
    def _normalize_sequence(raw: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return {
            "is_running": bool(raw.get("IsRunning", False)),
            "total_items": raw.get("TotalItems"),
            "current_index": raw.get("CurrentItemIndex"),
            "name": raw.get("Name"),
        }

    @staticmethod
    def _normalize_focuser(raw: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        return {
            "is_moving": bool(raw.get("IsMoving", False)),
            "position": raw.get("Position"),
            "temperature": raw.get("Temperature"),
        }

    @staticmethod
    def _derive_blockers(
        telescope: dict[str, Any],
        camera: dict[str, Any],
        sequence: dict[str, Any],
    ) -> list[dict[str, Any]]:
        blockers: list[dict[str, Any]] = []
        if telescope.get("is_connected") is False:
            blockers.append({"reason": "mount_disconnected"})
        if telescope.get("is_parked"):
            blockers.append({"reason": "mount_parked"})
        if camera.get("is_connected") is False:
            blockers.append({"reason": "camera_disconnected"})
        if camera.get("is_exposing"):
            blockers.append({"reason": "camera_exposing"})
        if sequence.get("is_running"):
            blockers.append({"reason": "sequence_running"})
        return blockers

    @staticmethod
    def _ready_to_expose(
        telescope: dict[str, Any],
        camera: dict[str, Any],
        sequence: dict[str, Any],
    ) -> bool:
        if telescope.get("is_connected") is False or camera.get("is_connected") is False:
            return False
        if telescope.get("is_parked") or telescope.get("is_slewing"):
            return False
        if camera.get("is_exposing"):
            return False
        if sequence.get("is_running"):
            return False
        return True

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

    def start_guiding(self) -> str:
        """Start the guider if infrastructure is available."""
        return self._request("GET", "/equipment/guider/start")

    def stop_guiding(self) -> str:
        """Stop the guider (called after exposures complete)."""
        return self._request("GET", "/equipment/guider/stop")


__all__ = ["NinaBridgeService"]
