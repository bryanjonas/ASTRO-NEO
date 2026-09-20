"""Autofocus orchestration -- Phase 4 of the direct_hardware branch.

Real-hardware sequencing on top of the validated math in autofocus.py:
move focuser to each position in a scan range, capture a short exposure,
measure median HFR, fit a V-curve, move to the fitted minimum.

NOT YET RUN against real hardware -- deliberately. Defaults here are
conservative (small step size, modest total scan range) per an explicit
request not to cycle the focuser through large or frequent moves; running
even the conservative default scan for the first time should be a
deliberate choice, not something exercised while writing this.
"""

from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.services.alpaca_camera_client import AlpacaCameraClient
from app.services.alpaca_focuser_client import AlpacaFocuserClient
from app.services.autofocus import FocusSample, fit_v_curve_minimum, measure_hfr

logger = logging.getLogger(__name__)


class AutofocusError(Exception):
    pass


def run_autofocus(
    step_size: int = 50,
    num_steps: int = 5,
    exposure_seconds: float = 2.0,
    move_settle_seconds: float = 1.0,
) -> dict:
    """Scan `num_steps` positions on each side of the focuser's current
    position (2*num_steps+1 total, spaced `step_size` apart), measuring
    HFR at each, and move to the fitted V-curve minimum.

    Conservative defaults: step_size=50 (out of a real MaxStep=12000 on
    this hardware) and num_steps=5 means a total scan span of only 500
    steps either side of center, in single deliberate moves -- not rapid
    pulsing, not large excursions. Raises AutofocusError rather than
    silently leaving the focuser somewhere unexpected if the scan doesn't
    produce a usable V-curve.
    """
    focuser = AlpacaFocuserClient(
        base_url=settings.alpaca_focuser_url,
        device_number=settings.alpaca_focuser_device_number,
        timeout=settings.alpaca_timeout,
    )
    camera = AlpacaCameraClient(
        base_url=settings.alpaca_camera_url,
        device_number=settings.alpaca_camera_device_number,
        timeout=settings.alpaca_timeout,
    )

    start_position = focuser.get_position()
    positions = [start_position + step_size * offset for offset in range(-num_steps, num_steps + 1)]

    samples: list[FocusSample] = []
    camera.set_binning(1, 1)
    camera.set_gain(settings.alpaca_camera_gain)

    try:
        for position in positions:
            focuser.move_and_wait(position, timeout=30.0)
            time.sleep(move_settle_seconds)

            camera.start_exposure(exposure_seconds, light=True)
            camera.wait_for_image_ready(timeout=exposure_seconds + 30.0)
            data = camera.get_image_array()

            median_hfr, star_count = measure_hfr(data)
            samples.append(FocusSample(position=position, median_hfr=median_hfr, star_count=star_count))
            logger.info(
                "Autofocus: position=%d median_hfr=%s stars=%d",
                position,
                f"{median_hfr:.2f}" if median_hfr is not None else "n/a",
                star_count,
            )
    except Exception:
        # Best-effort return to the starting position if the scan itself
        # breaks partway through -- don't leave the focuser stranded at
        # some arbitrary scan position.
        logger.warning("Autofocus scan failed partway through; returning to start position")
        try:
            focuser.move_and_wait(start_position, timeout=30.0)
        except Exception as exc:
            logger.error("Could not return focuser to start position %d: %s", start_position, exc)
        raise

    best_position = fit_v_curve_minimum(samples)
    if best_position is None:
        focuser.move_and_wait(start_position, timeout=30.0)
        raise AutofocusError(
            "V-curve fit did not produce a usable minimum -- focuser returned to start position. "
            f"Samples: {samples}"
        )

    focuser.move_and_wait(best_position, timeout=30.0)

    return {
        "start_position": start_position,
        "best_position": best_position,
        "samples": [(s.position, s.median_hfr, s.star_count) for s in samples],
    }


__all__ = ["run_autofocus", "AutofocusError"]
