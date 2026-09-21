"""All-sky polar alignment orchestration -- direct_hardware branch.

Captures two points, computes the mount's actual polar axis error, and
reports it -- see polar_alignment.py for the validated math core. This
module owns the real-hardware sequencing: slew, capture+solve, RA-only
re-slew, capture+solve again.

The commanded rotation passed into the math core comes from the MOUNT'S
OWN reported RA before/after the second slew, not from the two solved
positions -- deliberately, since inferring it from solved sky coordinates
would be circular with the very polar-alignment error being measured. A
mount's own RA-axis encoder delta for a same-declination retarget is a
much more trustworthy "known rotation angle" input.

NOT YET VERIFIED against a real star field (it's daytime -- see
polar_alignment.py's module docstring and tonight's other real-hardware
work for the established pattern of flagging this honestly). One specific
risk this hasn't resolved: the sign mapping between "mount-reported RA
decreased by X" and the math core's Alt/Az right-hand-rotation convention
is implemented according to the standard HA=LST-RA relationship below,
but hasn't been empirically cross-checked against a real, independently-
verified polar alignment reading. Treat the FIRST real run's reported
direction with appropriate skepticism -- cross-check against a polar
scope or other independent method before trusting the sign blindly.
"""

from __future__ import annotations

import logging
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
    pole before starting a measurement cycle.

    Confirmed live as the real cause of a failed measurement: the mount
    had been sitting at Dec~90 (parked near the pole, its normal idle
    state all night) when a bare measurement was attempted using "wherever
    the mount currently is" as point 1. Near the pole, a 60 degree rotation
    about the mount's own axis barely changes the actual pointing
    direction at all (you're rotating around an axis that nearly passes
    through your own pointing direction) -- which breaks the whole
    method's geometry (the solver correctly refused to answer: "No sign
    change found", rather than returning a wrong number).

    Az 90 (East) at Alt 50 sits safely inside this branch's configured
    open horizon arc (0-135 deg needs only 30+ deg altitude) and is a
    reasonable, moderate-altitude, moderate-hour-angle reference point --
    not claiming this is the mathematically optimal point (near-meridian
    is more classically recommended for azimuth sensitivity specifically),
    just "far enough from the pole that the method's geometry is sound".
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


def run_all_sky_polar_alignment(
    exposure_seconds: float = 5.0,
    rotation_deg: float = 60.0,
    settle_seconds: float = 3.0,
    reference_alt_deg: float = 50.0,
    reference_az_deg: float = 90.0,
) -> dict[str, Any]:
    """Run one full measurement cycle: slew to a fixed reference point
    away from the pole, capture+solve, RA-only re-slew by `rotation_deg`,
    capture+solve again, solve for the polar axis error.

    This does not adjust anything -- it's a measurement tool. Run it,
    physically adjust the mount's azimuth/altitude bolts per the reported
    direction, then run it again to confirm convergence (matching how
    SharpCap's equivalent live-feedback tool is used, just one measurement
    at a time rather than continuously)."""
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

    mount_info_before = mount.mount_info_raw()
    mount_ra_hours_before = float(mount_info_before["RightAscension"])
    mount_dec_before = float(mount_info_before["Declination"])

    # Retarget to the SAME reported declination, offset RA by
    # rotation_deg/15 hours -- keeping declination unchanged means a
    # well-behaved GEM only needs to move its RA axis to get there.
    target_ra_hours = (mount_ra_hours_before - rotation_deg / 15.0) % 24.0
    mount.slew(target_ra_hours * 15.0, mount_dec_before)
    mount.wait_for_mount_ready(timeout=120.0)
    time.sleep(settle_seconds)

    mount_info_after = mount.mount_info_raw()
    mount_ra_hours_after = float(mount_info_after["RightAscension"])

    # The mount's own RA-axis rotation, from its own encoder/reported
    # position -- not inferred from solved sky positions (see module
    # docstring for why that would be circular).
    ra_delta_hours = mount_ra_hours_before - mount_ra_hours_after
    # Handle wraparound (e.g. 23.9h -> 0.1h).
    if ra_delta_hours > 12.0:
        ra_delta_hours -= 24.0
    elif ra_delta_hours < -12.0:
        ra_delta_hours += 24.0
    commanded_rotation_deg = ra_delta_hours * 15.0

    point2 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=1)

    from app.core.site_config import load_site_config

    site = load_site_config()

    result = solve_polar_axis_error(
        ra1_deg=point1["ra_deg"],
        dec1_deg=point1["dec_deg"],
        time1=point1["time"],
        ra2_deg=point2["ra_deg"],
        dec2_deg=point2["dec_deg"],
        time2=point2["time"],
        commanded_rotation_deg=commanded_rotation_deg,
        site_lat_deg=site.latitude,
        site_lon_deg=site.longitude,
        site_height_m=site.altitude_m,
    )

    description = describe_adjustment(result["az_error_arcmin"], result["alt_error_arcmin"])
    logger.info("Polar alignment result: %s", description)

    return {**result, "description": description, "commanded_rotation_deg": commanded_rotation_deg}


# --- Continuous mode: repeat the measurement cycle in a background thread,
# so the reading keeps updating while you physically adjust the mount's
# azimuth/altitude bolts between cycles -- each cycle is a fully
# independent, self-contained measurement (fresh reference slew, fresh
# pair of shots), so it's fine that you're changing the physical axis
# between them; nothing here assumes the axis stayed put across cycles,
# only within one cycle's own two shots. ---

import threading

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop_flag = threading.Event()
_state: dict[str, Any] = {
    "running": False,
    "latest": None,
    "history": [],
    "error": None,
    "cycle_count": 0,
}
_MAX_HISTORY = 20


def get_polar_align_state() -> dict[str, Any]:
    with _lock:
        return {**_state, "history": list(_state["history"])}


def _continuous_loop(exposure_seconds: float, rotation_deg: float, reference_alt_deg: float, reference_az_deg: float) -> None:
    while not _stop_flag.is_set():
        try:
            result = run_all_sky_polar_alignment(
                exposure_seconds=exposure_seconds,
                rotation_deg=rotation_deg,
                reference_alt_deg=reference_alt_deg,
                reference_az_deg=reference_az_deg,
            )
            with _lock:
                _state["latest"] = result
                _state["error"] = None
                _state["cycle_count"] += 1
                _state["history"].append(result)
                if len(_state["history"]) > _MAX_HISTORY:
                    _state["history"] = _state["history"][-_MAX_HISTORY:]
            logger.info("Polar alignment cycle %d: %s", _state["cycle_count"], result["description"])
        except Exception as exc:
            logger.warning("Polar alignment cycle failed: %s", exc)
            with _lock:
                _state["error"] = str(exc)
            # A single failed cycle (e.g. a transient solve failure) isn't
            # fatal to the whole continuous session -- keep going rather
            # than silently stopping, but don't hammer real hardware in a
            # tight retry loop if something's persistently wrong.
            _stop_flag.wait(5.0)

    with _lock:
        _state["running"] = False


def start_continuous_polar_alignment(
    exposure_seconds: float = 5.0,
    rotation_deg: float = 60.0,
    reference_alt_deg: float = 50.0,
    reference_az_deg: float = 90.0,
) -> None:
    global _thread
    with _lock:
        if _state["running"]:
            raise PolarAlignmentError("Continuous polar alignment is already running")
        _state["running"] = True
        _state["error"] = None
        _state["cycle_count"] = 0
        _state["history"] = []
    _stop_flag.clear()
    _thread = threading.Thread(
        target=_continuous_loop,
        args=(exposure_seconds, rotation_deg, reference_alt_deg, reference_az_deg),
        daemon=True,
        name="polar-align-continuous",
    )
    _thread.start()


def stop_continuous_polar_alignment() -> None:
    _stop_flag.set()
    with _lock:
        _state["running"] = False


__all__ = [
    "run_all_sky_polar_alignment",
    "start_continuous_polar_alignment",
    "stop_continuous_polar_alignment",
    "get_polar_align_state",
]
