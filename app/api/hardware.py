"""Live hardware status -- mount, camera, guiding, focuser.

Reports on whichever backend each subsystem is actually configured to
use (settings.mount_backend / camera_backend / guiding_backend), rather
than assuming Alpaca -- this endpoint is meant to reflect the real, active
configuration, whether that's the known_targets-compatible NINA default
or an opted-in Alpaca/PHD2 setup. Each subsystem is queried independently
and degrades gracefully (reachable=False + an error message) rather than
letting one unreachable device take down the whole status response --
hardware being off/unreachable is a completely normal state for this
endpoint to report on, not an error condition for the endpoint itself.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hardware", tags=["hardware"])


def _nina_status_cached(cache: dict[str, Any]) -> dict[str, Any]:
    """NinaBridgeService.get_status() covers telescope+camera+focuser in
    one call -- fetch it at most once per request even if multiple
    subsystems are on the nina backend."""
    if "nina" not in cache:
        from app.services.nina_client import NinaBridgeService

        # Short timeout -- NinaBridgeService defaults to 300s (fine for
        # actual exposures, unacceptable for a quick status poll).
        cache["nina"] = NinaBridgeService(timeout=5.0).get_status()
    return cache["nina"]


def _mount_status(cache: dict[str, Any]) -> dict[str, Any]:
    backend = settings.mount_backend
    try:
        if backend == "alpaca":
            from app.services.alpaca_mount_client import AlpacaMountClient

            client = AlpacaMountClient(
                base_url=settings.alpaca_mount_url,
                device_number=settings.alpaca_mount_device_number,
                timeout=5.0,
            )
            return {"backend": backend, "reachable": True, **client.mount_info()}
        telescope = _nina_status_cached(cache)["nina_status"]["telescope"]
        return {"backend": backend, "reachable": True, **telescope}
    except Exception as exc:
        logger.debug("Mount status unavailable: %s", exc)
        return {"backend": backend, "reachable": False, "error": str(exc)}


def _camera_status(cache: dict[str, Any]) -> dict[str, Any]:
    backend = settings.camera_backend
    try:
        if backend == "alpaca":
            from app.services.alpaca_camera_client import AlpacaCameraClient

            client = AlpacaCameraClient(
                base_url=settings.alpaca_camera_url,
                device_number=settings.alpaca_camera_device_number,
                timeout=5.0,
            )
            caps = client.get_capabilities()
            return {
                "backend": backend,
                "reachable": True,
                "name": caps.get("name"),
                "ccd_temperature_c": client.get_ccd_temperature(),
                "gain": client.get_gain(),
            }
        camera = _nina_status_cached(cache)["nina_status"]["camera"]
        return {"backend": backend, "reachable": True, **camera}
    except Exception as exc:
        logger.debug("Camera status unavailable: %s", exc)
        return {"backend": backend, "reachable": False, "error": str(exc)}


def _guiding_status(cache: dict[str, Any]) -> dict[str, Any]:
    backend = settings.guiding_backend
    if backend == "none":
        return {"backend": backend, "reachable": True, "app_state": "disabled"}
    try:
        if backend == "phd2":
            from app.services.phd2_client import Phd2Client

            with Phd2Client(host=settings.phd2_host, port=settings.phd2_port, timeout=5.0) as client:
                return {
                    "backend": backend,
                    "reachable": True,
                    "app_state": client.app_state,
                    "connected": client.get_connected(),
                    "calibrated": client.get_calibrated(),
                    "equipment": client.get_current_equipment(),
                }
        from app.services.nina_client import NinaBridgeService

        guider = NinaBridgeService(timeout=5.0).guider_info()
        return {"backend": backend, "reachable": guider is not None, **(guider or {})}
    except Exception as exc:
        logger.debug("Guiding status unavailable: %s", exc)
        return {"backend": backend, "reachable": False, "error": str(exc)}


def _focuser_status(cache: dict[str, Any]) -> dict[str, Any]:
    try:
        from app.services.alpaca_focuser_client import AlpacaFocuserClient

        client = AlpacaFocuserClient(
            base_url=settings.alpaca_focuser_url,
            device_number=settings.alpaca_focuser_device_number,
            timeout=5.0,
        )
        return {
            "backend": "alpaca",
            "reachable": True,
            "position": client.get_position(),
            "is_moving": client.is_moving(),
            "max_step": client.get_max_step(),
        }
    except Exception as exc:
        logger.debug("Focuser status unavailable: %s", exc)
        return {"backend": "alpaca", "reachable": False, "error": str(exc)}


@router.get("/status")
def hardware_status() -> dict[str, Any]:
    cache: dict[str, Any] = {}
    return {
        "mount": _mount_status(cache),
        "camera": _camera_status(cache),
        "guiding": _guiding_status(cache),
        "focuser": _focuser_status(cache),
    }
