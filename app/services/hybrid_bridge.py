"""Mount/camera backend selection -- Phase 1 of the direct_hardware branch.

sequential_capture.py talks to a single `self.nina` object for both mount
and camera calls. Camera control isn't built for Alpaca yet (that's Phase
2 -- see documentation/DIRECT_HARDWARE_DESIGN.md), so this can't simply
swap NinaBridgeService for AlpacaMountClient wholesale: AlpacaMountClient
only implements the mount subset. HybridBridge presents the same method
surface sequential_capture.py actually calls (confirmed via direct
inspection, not guessed -- see the grep in the commit that added this),
routing mount calls to whichever backend `settings.mount_backend` selects
and camera calls to NINA unconditionally until Phase 2 lands.

Guiding is being dropped per this branch's design decision (the AM-3's
harmonic drive makes unguided tracking a reasonable bet) -- on the alpaca
backend, start_guiding_best_effort() is a no-op rather than an error.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.services.alpaca_mount_client import AlpacaMountClient, TRACKING_RATE_SIDEREAL
from app.services.nina_client import NinaBridgeService

logger = logging.getLogger(__name__)


class HybridBridge:
    """Routes mount calls to NINA or Alpaca per settings.mount_backend;
    camera calls always go to NINA (Phase 2 hasn't built Alpaca camera
    control yet)."""

    def __init__(
        self,
        nina_client: NinaBridgeService | None = None,
        alpaca_client: AlpacaMountClient | None = None,
    ) -> None:
        self._nina = nina_client or NinaBridgeService()
        self._alpaca = alpaca_client or AlpacaMountClient(
            base_url=settings.alpaca_mount_url,
            device_number=settings.alpaca_mount_device_number,
            timeout=settings.alpaca_timeout,
        )
        logger.info("HybridBridge: mount_backend=%s (camera always via NINA)", settings.mount_backend)

    @property
    def _mount(self) -> Any:
        return self._alpaca if settings.mount_backend == "alpaca" else self._nina

    # --- Mount: routed by settings.mount_backend ---

    def slew(self, ra_deg: float, dec_deg: float) -> str:
        return self._mount.slew(ra_deg, dec_deg)

    def wait_for_mount_ready(self, timeout: float = 180.0, **kwargs: Any) -> None:
        return self._mount.wait_for_mount_ready(timeout=timeout, **kwargs)

    def sync_mount(self, ra_deg: float | None = None, dec_deg: float | None = None) -> str:
        return self._mount.sync_mount(ra_deg=ra_deg, dec_deg=dec_deg)

    def mount_info(self) -> dict[str, Any]:
        return self._mount.mount_info()

    def set_tracking(self, mode: int) -> str:
        if settings.mount_backend == "alpaca":
            if mode != TRACKING_RATE_SIDEREAL:
                logger.warning(
                    "AlpacaMountClient backend only supports sidereal tracking "
                    "(requested mode=%s); using sidereal anyway.",
                    mode,
                )
            self._alpaca.ensure_sidereal_tracking()
            return "tracking"
        return self._nina.set_tracking(mode)

    def start_guiding_best_effort(self, timeout: float = 2.0) -> bool:
        if settings.mount_backend == "alpaca":
            logger.debug("Guiding dropped on alpaca backend; no-op.")
            return True
        return self._nina.start_guiding_best_effort(timeout=timeout)

    # --- Camera: always NINA until Phase 2 ---

    def start_exposure(self, **kwargs: Any) -> str:
        return self._nina.start_exposure(**kwargs)

    def wait_for_camera_idle(self, timeout: float = 120.0, **kwargs: Any) -> None:
        return self._nina.wait_for_camera_idle(timeout=timeout, **kwargs)


def build_mount_camera_bridge() -> NinaBridgeService | HybridBridge:
    """Construct whichever bridge sequential_capture.py should use.

    Pure NinaBridgeService when mount_backend="nina" (the default, and the
    only path known_targets ever exercises) -- no behavior change at all
    for that branch/config. HybridBridge only when explicitly opted into
    the alpaca mount backend.
    """
    if settings.mount_backend == "alpaca":
        return HybridBridge()
    return NinaBridgeService()


__all__ = ["HybridBridge", "build_mount_camera_bridge"]
