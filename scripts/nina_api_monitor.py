"""Smoke tests for the NINA bridge endpoints the automation relies on."""

from __future__ import annotations

import logging
import math
from datetime import datetime

from app.services.nina_client import NinaBridgeService

logger = logging.getLogger(__name__)


def _log_status(label: str, bridge: NinaBridgeService) -> None:
    status = bridge.get_status()
    nina_status = status.get("nina_status", {})
    telescope = nina_status.get("telescope", {})
    camera = nina_status.get("camera", {})
    logger.info(
        "%s | telescope slewing=%s ready=%.3f×%.3f camera exposing=%s",
        label,
        telescope.get("is_slewing"),
        telescope.get("ra_deg"),
        telescope.get("dec_deg"),
        camera.get("is_exposing"),
    )


def test_mount_slew(bridge: NinaBridgeService) -> None:
    info = bridge._request("GET", "/equipment/mount/info")
    coords = info.get("Coordinates", {})
    current_ra = float(coords.get("RADegrees", info.get("RightAscension", 0.0) * 15))
    current_dec = float(coords.get("Dec", info.get("Declination", 0.0)))
    delta_ra = 0.1
    delta_dec = 0.05

    target_ra = (current_ra + delta_ra) % 360
    target_dec = max(min(current_dec + delta_dec, 89.0), -89.0)

    logger.info(
        "Testing mount slew → RA %.4f°, Dec %.4f° (was %.4f°/%.4f°)",
        target_ra,
        target_dec,
        current_ra,
        current_dec,
    )

    bridge.slew(target_ra, target_dec)
    bridge.wait_for_mount_ready(timeout=60.0)
    _log_status("post-slew", bridge)


def test_camera_capture(bridge: NinaBridgeService) -> None:
    camera_info = bridge._request("GET", "/equipment/camera/info")
    if not camera_info.get("Connected"):
        logger.warning("Camera is not connected; skipping capture test")
        return
    bridge.wait_for_camera_idle(timeout=30.0)
    logger.info("Camera idle, issuing quick test exposure")

    try:
        result = bridge.start_exposure(
            filter_name="L",
            binning=2,
            exposure_seconds=1.0,
            target="NINA-API-TEST",
        )
    except Exception as exc:
        logger.error("Capture request failed: %s", exc)
        return

    file_path = result.get("file")
    platesolve = result.get("platesolve")
    logger.info(
        "Capture response → saved=%s solved=%s file=%s",
        bool(file_path),
        bool(platesolve),
        file_path,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bridge = NinaBridgeService()

    _log_status("startup", bridge)
    test_mount_slew(bridge)
    test_camera_capture(bridge)
    _log_status("finish", bridge)


if __name__ == "__main__":
    main()
