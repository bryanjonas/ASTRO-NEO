"""Mount/camera/guiding backend selection -- direct_hardware branch.

sequential_capture.py talks to a single `self.nina` object for mount,
camera, and guiding calls. Camera control isn't built for Alpaca yet
(Phase 2 handles that separately -- see DIRECT_HARDWARE_DESIGN.md), so
this can't simply swap NinaBridgeService for AlpacaMountClient wholesale:
AlpacaMountClient only implements the mount subset. HybridBridge presents
the same method surface sequential_capture.py actually calls (confirmed
via direct inspection, not guessed -- see the grep in the commit that
added this), routing:

- mount calls to NINA or Alpaca per settings.mount_backend
- camera calls to NINA unconditionally until Phase 2's camera backend
  gets wired in here too
- guiding calls to NINA's existing wrapper, PHD2 directly (see
  phd2_client.py), or nowhere at all, per settings.guiding_backend --
  independent of mount_backend, since PHD2 is a real, separate decision
  from which backend controls the mount.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.services.alpaca_mount_client import AlpacaMountClient, TRACKING_RATE_SIDEREAL
from app.services.nina_client import NinaBridgeService

logger = logging.getLogger(__name__)


class HybridBridge:
    """Routes mount calls to NINA or Alpaca per settings.mount_backend,
    guiding calls to NINA/PHD2/nowhere per settings.guiding_backend;
    camera calls always go to NINA (Phase 2 hasn't wired its camera
    backend in here yet)."""

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
        logger.info(
            "HybridBridge: mount_backend=%s guiding_backend=%s (camera always via NINA)",
            settings.mount_backend,
            settings.guiding_backend,
        )

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

    # --- Guiding: routed by settings.guiding_backend, independent of
    # mount_backend ---

    def start_guiding_best_effort(self, timeout: float = 2.0) -> bool:
        backend = settings.guiding_backend
        if backend == "none":
            logger.debug("Guiding disabled (guiding_backend=none); no-op.")
            return True
        if backend == "phd2":
            from app.services.phd2_client import Phd2Client, Phd2Error

            try:
                with Phd2Client(
                    host=settings.phd2_host, port=settings.phd2_port, timeout=settings.phd2_timeout
                ) as phd2:
                    return phd2.guide_and_wait_for_settle(
                        settle_pixels=settings.phd2_settle_pixels,
                        settle_time=settings.phd2_settle_time,
                        settle_timeout=settings.phd2_settle_timeout,
                    )
            except Phd2Error as exc:
                logger.warning("PHD2 guiding failed (non-fatal, continuing): %s", exc)
                return False
        return self._nina.start_guiding_best_effort(timeout=timeout)

    # --- Camera: always NINA until Phase 2 wires its backend in here ---

    def start_exposure(self, **kwargs: Any) -> str:
        return self._nina.start_exposure(**kwargs)

    def wait_for_camera_idle(self, timeout: float = 120.0, **kwargs: Any) -> None:
        return self._nina.wait_for_camera_idle(timeout=timeout, **kwargs)


def build_mount_camera_bridge() -> NinaBridgeService | HybridBridge:
    """Construct whichever bridge sequential_capture.py should use.

    Pure NinaBridgeService when both mount_backend="nina" and
    guiding_backend="nina" (the defaults, and the only path known_targets
    ever exercises) -- no behavior change at all for that branch/config.
    HybridBridge whenever either is opted away from NINA.
    """
    if settings.mount_backend == "alpaca" or settings.guiding_backend != "nina":
        return HybridBridge()
    return NinaBridgeService()


__all__ = ["HybridBridge", "build_mount_camera_bridge"]
