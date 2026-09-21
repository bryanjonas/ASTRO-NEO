"""All-sky polar alignment orchestration -- direct_hardware branch.

Two-phase design, matching the standard real-world workflow (confirmed
against a real run tonight that the previous "repeat the full slew cycle
forever" design was wrong -- it kept re-slewing across a wide arc every
~1 minute with no pause, which is exactly what looked like "the mount
going all over the place" rather than a controlled measurement):

Phase 1 -- Calibrate (runs once): three shots ~30 degrees apart in RA
(same declination), computing the polar axis error from the two
widest-separated points. Two slews total, not repeated.

Phase 2 -- Monitor (stays put): after calibration, the mount is NOT
slewed again. Repeatedly capture+solve at the same fixed pointing while
you physically adjust the azimuth/altitude bolts, reporting how far the
solved position has drifted from the first monitor-phase reading -- a
direct, honest readout of your adjustment's effect, not a re-derived
az/alt breakdown (that would need re-deriving the drift-alignment
geometry for an arbitrary, uncalibrated moment, which hasn't been done
or validated the way the two-point axis solver has -- see
polar_alignment.py). Watch this drift accumulate roughly in line with
the calibration's reported error, then stop.

See polar_alignment.py for the validated math core (the two-point axis
solver) used only in the calibration phase.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from astropy.time import Time

from app.core.config import settings
from app.services.alpaca_camera_client import AlpacaCameraClient
from app.services.alpaca_fits_writer import CaptureContext, write_capture_fits
from app.services.alpaca_mount_client import AlpacaMountClient
from app.services.polar_alignment import (
    PolarAlignmentError,
    describe_adjustment,
    solve_polar_axis_error,
)
from app.services.solver import SolveError, solve_fits

logger = logging.getLogger(__name__)


def _capture_and_solve(
    mount: AlpacaMountClient,
    camera: AlpacaCameraClient,
    exposure_seconds: float,
    target_name: str,
    frame_index: int,
) -> dict[str, Any]:
    """One exposure + blind solve, returning solved ra_deg/dec_deg and the
    UTC capture time. Raises on capture or solve failure -- this is a
    diagnostic tool run interactively, not an unattended pipeline step, so
    surfacing failures directly (rather than a best-effort fallback) is
    the right behavior here."""
    caps = camera.get_capabilities()
    camera.set_binning(1, 1)
    camera.set_gain(settings.alpaca_camera_gain)
    camera.start_exposure(exposure_seconds, light=True)
    camera.wait_for_image_ready(timeout=exposure_seconds + 30.0)
    data = camera.get_image_array()

    capture_time = Time.now()
    ctx = CaptureContext(
        target_name=target_name,
        exposure_seconds=exposure_seconds,
        date_obs_utc=capture_time.to_datetime(),
        frame_index=frame_index,
        binning=1,
        gain=settings.alpaca_camera_gain,
        electrons_per_adu=caps.get("electrons_per_adu"),
        pixel_size_um=caps.get("pixel_size_x"),
        instrument_name=caps.get("name", ""),
        bayer_offset_x=caps.get("bayer_offset_x", 0) or 0,
        bayer_offset_y=caps.get("bayer_offset_y", 0) or 0,
        ccd_temperature_c=camera.get_ccd_temperature(),
    )
    fits_path = write_capture_fits(data, ctx, output_root=settings.nina_images_path)
    logger.info("Polar alignment capture %d written: %s", frame_index, fits_path)

    try:
        result = solve_fits(fits_path=str(fits_path), ra_hint=None, dec_hint=None, radius_deg=None)
    except SolveError as exc:
        raise PolarAlignmentError(f"Plate solve failed on capture {frame_index}: {exc}") from exc

    solution = result["solution"]
    return {
        "ra_deg": solution["ra_deg"],
        "dec_deg": solution["dec_deg"],
        "time": capture_time,
    }


def _slew_to_reference_point(mount: AlpacaMountClient, alt_deg: float = 50.0, az_deg: float = 90.0) -> None:
    """Slew to a fixed Alt/Az reference point well away from the celestial
    pole before starting calibration.

    Confirmed live as a real cause of a failed measurement: the mount
    had been sitting at Dec~90 (parked near the pole, its normal idle
    state) when a bare measurement was attempted using "wherever the
    mount currently is" as point 1 -- near the pole, a large rotation
    about the mount's own axis barely changes the actual pointing
    direction at all, breaking the whole method's geometry.

    Az 90 (East) at Alt 50 sits safely inside this branch's configured
    open horizon arc (0-135 deg needs only 30+ deg altitude).
    """
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    import astropy.units as u
    from app.core.site_config import load_site_config

    site = load_site_config()
    location = EarthLocation(lat=site.latitude * u.deg, lon=site.longitude * u.deg, height=site.altitude_m * u.m)
    altaz = AltAz(alt=alt_deg * u.deg, az=az_deg * u.deg, obstime=Time.now(), location=location)
    radec = SkyCoord(altaz).icrs
    mount.slew(float(radec.ra.deg), float(radec.dec.deg))
    mount.wait_for_mount_ready(timeout=120.0)


def _slew_ra_only(mount: AlpacaMountClient, ra_hours: float, dec_deg: float, offset_deg: float) -> float:
    """Slew to the same declination, offset by offset_deg of RA. Returns
    the mount's own post-slew RA (hours) for computing the real achieved
    rotation from the mount's own encoder, not the commanded value."""
    target_ra_hours = (ra_hours - offset_deg / 15.0) % 24.0
    mount.slew(target_ra_hours * 15.0, dec_deg)
    mount.wait_for_mount_ready(timeout=120.0)
    return float(mount.mount_info_raw()["RightAscension"])


def _ra_delta_deg(ra_hours_before: float, ra_hours_after: float) -> float:
    delta_hours = ra_hours_before - ra_hours_after
    if delta_hours > 12.0:
        delta_hours -= 24.0
    elif delta_hours < -12.0:
        delta_hours += 24.0
    return delta_hours * 15.0


def calibrate(
    exposure_seconds: float = 5.0,
    step_deg: float = 30.0,
    settle_seconds: float = 3.0,
    reference_alt_deg: float = 50.0,
    reference_az_deg: float = 90.0,
) -> dict[str, Any]:
    """Three shots step_deg apart in RA (same declination): slew to a
    reference point, capture+solve, slew +step_deg twice more,
    capture+solve each time. Computes the axis error from the two
    widest-separated points (points 1 and 3, 2*step_deg apart). Leaves
    the mount at point 3's pointing -- no slew happens after this
    returns; call monitor_once() repeatedly from there.
    """
    mount = AlpacaMountClient(
        base_url=settings.alpaca_mount_url,
        device_number=settings.alpaca_mount_device_number,
        timeout=settings.alpaca_timeout,
    )
    camera = AlpacaCameraClient(
        base_url=settings.alpaca_camera_url,
        device_number=settings.alpaca_camera_device_number,
        timeout=settings.alpaca_timeout,
    )

    _slew_to_reference_point(mount, reference_alt_deg, reference_az_deg)
    time.sleep(settle_seconds)
    point1 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=0)

    mount_info_1 = mount.mount_info_raw()
    ra_hours_1 = float(mount_info_1["RightAscension"])
    dec_deg = float(mount_info_1["Declination"])

    ra_hours_2 = _slew_ra_only(mount, ra_hours_1, dec_deg, step_deg)
    time.sleep(settle_seconds)
    point2 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=1)

    ra_hours_3 = _slew_ra_only(mount, ra_hours_1, dec_deg, 2 * step_deg)
    time.sleep(settle_seconds)
    point3 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=2)

    commanded_rotation_deg = _ra_delta_deg(ra_hours_1, ra_hours_3)

    from app.core.site_config import load_site_config

    site = load_site_config()
    result = solve_polar_axis_error(
        ra1_deg=point1["ra_deg"],
        dec1_deg=point1["dec_deg"],
        time1=point1["time"],
        ra2_deg=point3["ra_deg"],
        dec2_deg=point3["dec_deg"],
        time2=point3["time"],
        commanded_rotation_deg=commanded_rotation_deg,
        site_lat_deg=site.latitude,
        site_lon_deg=site.longitude,
        site_height_m=site.altitude_m,
    )
    description = describe_adjustment(result["az_error_arcmin"], result["alt_error_arcmin"])
    logger.info("Polar alignment calibration: %s", description)

    return {
        **result,
        "description": description,
        "commanded_rotation_deg": commanded_rotation_deg,
        "last_heading": {"ra_deg": point3["ra_deg"], "dec_deg": point3["dec_deg"]},
    }


def monitor_once(exposure_seconds: float = 5.0) -> dict[str, Any]:
    """One capture+solve at whatever the mount is CURRENTLY pointed at --
    no slew, no mount commands at all. Used repeatedly during the monitor
    phase while you physically adjust the mount by hand."""
    mount = AlpacaMountClient(
        base_url=settings.alpaca_mount_url,
        device_number=settings.alpaca_mount_device_number,
        timeout=settings.alpaca_timeout,
    )
    camera = AlpacaCameraClient(
        base_url=settings.alpaca_camera_url,
        device_number=settings.alpaca_camera_device_number,
        timeout=settings.alpaca_timeout,
    )
    point = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN_MONITOR", frame_index=0)
    return {"ra_deg": point["ra_deg"], "dec_deg": point["dec_deg"]}


def _separation_arcsec(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    import math

    cos_dec = math.cos(math.radians((dec1 + dec2) / 2.0))
    d_ra = (ra2 - ra1) * cos_dec
    d_dec = dec2 - dec1
    return math.sqrt(d_ra**2 + d_dec**2) * 3600.0


# --- Continuous session: calibrate once, then repeatedly monitor in
# place (no further slewing) until stopped. ---

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_flag = threading.Event()
_state: dict[str, Any] = {
    "running": False,
    "phase": "idle",  # "idle" | "calibrating" | "monitoring"
    "calibration": None,
    "monitor_baseline": None,
    "monitor_latest": None,
    "monitor_drift_arcsec": None,
    "monitor_count": 0,
    "error": None,
}


def get_polar_align_state() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _run_loop(exposure_seconds: float, step_deg: float, reference_alt_deg: float, reference_az_deg: float) -> None:
    try:
        with _lock:
            _state["phase"] = "calibrating"
        calibration = calibrate(
            exposure_seconds=exposure_seconds,
            step_deg=step_deg,
            reference_alt_deg=reference_alt_deg,
            reference_az_deg=reference_az_deg,
        )
        with _lock:
            _state["calibration"] = calibration
            _state["phase"] = "monitoring"
    except Exception as exc:
        logger.warning("Polar alignment calibration failed: %s", exc)
        with _lock:
            _state["error"] = str(exc)
            _state["running"] = False
            _state["phase"] = "idle"
        return

    baseline = None
    count = 0
    while not _stop_flag.is_set():
        try:
            point = monitor_once(exposure_seconds=exposure_seconds)
            with _lock:
                if baseline is None:
                    baseline = point
                    _state["monitor_baseline"] = baseline
                _state["monitor_latest"] = point
                _state["monitor_drift_arcsec"] = _separation_arcsec(
                    baseline["ra_deg"], baseline["dec_deg"], point["ra_deg"], point["dec_deg"]
                )
                count += 1
                _state["monitor_count"] = count
                _state["error"] = None
        except Exception as exc:
            logger.warning("Polar alignment monitor capture failed: %s", exc)
            with _lock:
                _state["error"] = str(exc)
            _stop_flag.wait(5.0)

    with _lock:
        _state["running"] = False
        _state["phase"] = "idle"


def start_continuous_polar_alignment(
    exposure_seconds: float = 5.0,
    step_deg: float = 30.0,
    reference_alt_deg: float = 50.0,
    reference_az_deg: float = 90.0,
) -> None:
    global _thread
    with _lock:
        if _state["running"]:
            raise PolarAlignmentError("Polar alignment is already running")
        _state["running"] = True
        _state["phase"] = "calibrating"
        _state["calibration"] = None
        _state["monitor_baseline"] = None
        _state["monitor_latest"] = None
        _state["monitor_drift_arcsec"] = None
        _state["monitor_count"] = 0
        _state["error"] = None
    _stop_flag.clear()
    _thread = threading.Thread(
        target=_run_loop,
        args=(exposure_seconds, step_deg, reference_alt_deg, reference_az_deg),
        daemon=True,
        name="polar-align",
    )
    _thread.start()


def stop_continuous_polar_alignment() -> None:
    _stop_flag.set()
    with _lock:
        _state["running"] = False
        _state["phase"] = "idle"


__all__ = [
    "calibrate",
    "monitor_once",
    "start_continuous_polar_alignment",
    "stop_continuous_polar_alignment",
    "get_polar_align_state",
]
