"""All-sky polar alignment orchestration -- direct_hardware branch.

Two-phase design, matching the standard real-world workflow (confirmed
against a real run tonight that the previous "repeat the full slew cycle
forever" design was wrong -- it kept re-slewing across a wide arc every
~1 minute with no pause, which is exactly what looked like "the mount
going all over the place" rather than a controlled measurement):

Phase 1 -- Calibrate (runs once): three shots ~20 degrees apart in RA
(same declination), starting from wherever the mount is already
pointed (point yourself away from the pole first -- see
_MAX_ABS_DEC_FOR_CALIBRATION_DEG). Computes the polar axis error from
the two widest-separated points. Two slews total, not repeated.

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
    on_step: Any = None,
) -> dict[str, Any]:
    """One exposure + blind solve, returning solved ra_deg/dec_deg and the
    UTC capture time. Raises on capture or solve failure -- this is a
    diagnostic tool run interactively, not an unattended pipeline step, so
    surfacing failures directly (rather than a best-effort fallback) is
    the right behavior here."""
    if on_step:
        on_step(f"Exposing for {exposure_seconds:.0f}s...")
    caps = camera.get_capabilities()
    camera.set_binning(1, 1)
    camera.set_gain(settings.alpaca_camera_gain)
    camera.start_exposure(exposure_seconds, light=True)
    camera.wait_for_image_ready(timeout=exposure_seconds + 30.0)
    data = camera.get_image_array()
    if on_step:
        on_step("Plate-solving the image...")

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


_MAX_ABS_DEC_FOR_CALIBRATION_DEG = 80.0
"""Real all-sky polar alignment tools start shot 1 from wherever the
mount is already pointed rather than force-relocating it -- SharpCap's
2-point method uses a ~90 deg RA rotation, ASIAIR's up-to-3-point method
uses ~60 deg with declination held fixed, and neither auto-repoints you
first (see https://docs.sharpcap.co.uk/3.2/18_PolarAlignment.htm and
https://bbs.zwoastro.com/d/11580-polar-alignment-without-polaris).
calibrate() below matches that: point 1 is whatever's already framed.

Near the pole, though, a rotation about the mount's own RA axis barely
changes the actual pointing direction at all (rotating around an axis
that nearly passes through your own pointing direction), which breaks
this method's geometry regardless of which real tool implements it --
confirmed live: the solver correctly refused to answer ("No sign change
found...") starting from Dec ~= 90. Rather than silently slewing you
somewhere else the way an earlier version of this did, calibrate() just
refuses and asks you to repoint further from the pole yourself first."""


_MAX_DEC_DRIFT_AFTER_JOG_DEG = 0.3
"""Caught live: a coordinate-based 'same declination' GOTO that happened
to cross the local meridian resulted in a genuinely different
declination afterward (~24 deg off, not just a large-but-correct pier
flip) -- confirmed by checking hour angle (siderealtime - RA) before/
after the run: it crossed zero exactly where the bad jump occurred.
That's a real mount/driver GOTO bug, not just an alarming-looking flip,
and it's what was producing "giant moves".

Fixed by not using a coordinate GOTO for the RA-only re-slew at all --
_rotate_ra_axis_by() below uses ASCOM MoveAxis instead: a raw axis-rate
command with no coordinate transform and no meridian/pier-flip logic
involved, so it can't trip this bug. Declination should therefore be
completely untouched (not just "close"); this tolerance is tight
because any drift here means something unexpected is happening, not a
flip to tolerate."""

_JOG_PROBE_DEG = 1.5
"""A small direction-finding move before committing to the full
rotation -- MoveAxis's rate sign convention isn't something to trust
blindly for an untested driver, so this measures it empirically via the
mount's own RA readback rather than assuming."""


def _signed_delta_deg(start_deg: float, current_deg: float) -> float:
    delta = current_deg - start_deg
    while delta > 180.0:
        delta -= 360.0
    while delta < -180.0:
        delta += 360.0
    return delta


def _pick_jog_rate_deg_per_sec(mount: AlpacaMountClient) -> float:
    """Use a rate within the driver's own reported MoveAxis range for the
    RA axis, rather than assuming one blindly -- moderate (target ~2
    deg/sec) so the closed-loop stop below is responsive, but never
    exceeding what the driver actually allows."""
    from app.services.alpaca_mount_client import AXIS_PRIMARY

    rates = mount.get_axis_rates(AXIS_PRIMARY)
    max_rate = max((r["Maximum"] for r in rates), default=0.0)
    if max_rate <= 0.0:
        raise PolarAlignmentError(
            "Mount reports no usable MoveAxis rate for the RA axis -- cannot do a raw axis rotation."
        )
    return min(2.0, max_rate)


def _stop_ra_axis(mount: AlpacaMountClient) -> None:
    from app.services.alpaca_mount_client import AXIS_PRIMARY

    mount.move_axis(AXIS_PRIMARY, 0.0)


def _rotate_ra_axis_by(mount: AlpacaMountClient, offset_deg: float, on_step: Any = None) -> float:
    """Rotate the RA axis west by offset_deg (declination untouched) using
    a raw ASCOM MoveAxis rate command -- no coordinate GOTO, no
    meridian/pier-flip logic at all, so it can't trip the bug documented
    above. Closed-loop: polls the mount's own RA readback and stops
    itself, rather than trusting a computed time*rate estimate. The RA
    axis is guaranteed stopped (rate 0) before this returns OR raises.

    Returns the mount's own post-rotation RA (hours). Raises if
    declination moved at all (see _MAX_DEC_DRIFT_AFTER_JOG_DEG), if the
    mount can't report a usable MoveAxis rate, or if a direction-finding
    probe shows no motion at all."""
    from app.services.alpaca_mount_client import AXIS_PRIMARY

    def _step(msg: str) -> None:
        if on_step:
            on_step(msg)

    if not mount.can_move_axis(AXIS_PRIMARY):
        raise PolarAlignmentError("Mount reports it cannot MoveAxis on the RA axis -- cannot do a raw axis rotation.")

    rate = _pick_jog_rate_deg_per_sec(mount)
    dec_before = float(mount.mount_info_raw()["Declination"])
    start_ra_deg = float(mount.mount_info_raw()["RightAscension"]) * 15.0
    target_total_delta_deg = -abs(offset_deg)  # west, matching the previous slew convention

    poll_interval = 0.3
    try:
        _step(f"Probing RA-axis jog direction ({rate:.2f} deg/sec)...")
        mount.move_axis(AXIS_PRIMARY, rate)
        probe_deadline = time.time() + max(15.0, (_JOG_PROBE_DEG / rate) * 5)
        probe_delta = 0.0
        while time.time() < probe_deadline:
            time.sleep(poll_interval)
            current_ra_deg = float(mount.mount_info_raw()["RightAscension"]) * 15.0
            probe_delta = _signed_delta_deg(start_ra_deg, current_ra_deg)
            if abs(probe_delta) >= _JOG_PROBE_DEG:
                break
        _stop_ra_axis(mount)
        time.sleep(1.0)

        if abs(probe_delta) < 0.05:
            raise PolarAlignmentError(
                "RA axis didn't move at all during the direction-finding probe -- refusing to "
                "continue a raw axis rotation blind."
            )
        positive_rate_sign = 1.0 if probe_delta > 0 else -1.0

        remaining_deg = target_total_delta_deg - probe_delta
        jog_rate = rate * (positive_rate_sign if remaining_deg > 0 else -positive_rate_sign)

        _step(f"Rotating RA axis {abs(offset_deg):.0f}° west (raw axis move, declination untouched)...")
        mount.move_axis(AXIS_PRIMARY, jog_rate)
        hard_deadline = time.time() + max(90.0, (abs(offset_deg) / rate) * 6)
        total_delta = probe_delta
        while time.time() < hard_deadline:
            time.sleep(poll_interval)
            current_ra_deg = float(mount.mount_info_raw()["RightAscension"]) * 15.0
            total_delta = _signed_delta_deg(start_ra_deg, current_ra_deg)
            reached = total_delta <= target_total_delta_deg if target_total_delta_deg < 0 else total_delta >= target_total_delta_deg
            if reached:
                break
        else:
            raise PolarAlignmentError(
                f"RA-axis jog timed out before reaching the target ({total_delta:.1f} of "
                f"{target_total_delta_deg:.1f} deg) -- stopping the axis and aborting."
            )
    finally:
        _stop_ra_axis(mount)

    time.sleep(1.5)  # settle after a mechanical stop, before the next capture

    info = mount.mount_info_raw()
    actual_dec_deg = float(info["Declination"])
    if abs(actual_dec_deg - dec_before) > _MAX_DEC_DRIFT_AFTER_JOG_DEG:
        raise PolarAlignmentError(
            f"After a raw RA-axis rotation, declination moved from {dec_before:.2f} to "
            f"{actual_dec_deg:.2f} deg -- it should be completely untouched by MoveAxis. Aborting "
            "rather than trusting this reading."
        )
    return float(info["RightAscension"])


def _ra_delta_deg(ra_hours_before: float, ra_hours_after: float) -> float:
    delta_hours = ra_hours_before - ra_hours_after
    if delta_hours > 12.0:
        delta_hours -= 24.0
    elif delta_hours < -12.0:
        delta_hours += 24.0
    return delta_hours * 15.0


def calibrate(
    exposure_seconds: float = 5.0,
    step_deg: float = 20.0,
    settle_seconds: float = 3.0,
    on_step: Any = None,
) -> dict[str, Any]:
    """Three shots step_deg apart in RA (same declination), starting from
    wherever the mount is already pointed: capture+solve, slew +step_deg
    twice more, capture+solve each time. Computes the axis error from the
    two widest-separated points (points 1 and 3, 2*step_deg apart).
    Leaves the mount at point 3's pointing -- no slew happens after this
    returns; call monitor_once() repeatedly from there.

    on_step, if given, is called with a short human-readable string at
    each stage -- lets a caller (see _run_loop below) surface something
    more useful than a static "calibrating" label while this runs.

    Point yourself away from the celestial pole before starting --
    Dec beyond +/-80 is refused outright (see
    _MAX_ABS_DEC_FOR_CALIBRATION_DEG above)."""
    def _step(msg: str) -> None:
        if on_step:
            on_step(msg)

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

    _step("Reading current mount position...")
    mount_info_1 = mount.mount_info_raw()
    ra_hours_1 = float(mount_info_1["RightAscension"])
    dec_deg = float(mount_info_1["Declination"])
    if abs(dec_deg) > _MAX_ABS_DEC_FOR_CALIBRATION_DEG:
        raise PolarAlignmentError(
            f"Mount is pointed at Dec {dec_deg:.1f} deg, too close to the celestial pole for this "
            f"method's geometry to work (needs |Dec| <= {_MAX_ABS_DEC_FOR_CALIBRATION_DEG:.0f}). "
            "Slew to a spot further from the pole and try again."
        )

    _step(f"Shot 1 of 3: imaging current position (Dec {dec_deg:.1f}°), no slew yet...")
    point1 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=0, on_step=_step)
    _step(
        f"Shot 1 solved at RA {point1['ra_deg']:.2f}°/Dec {point1['dec_deg']:.2f}°. "
        f"Rotating {step_deg:.0f}° in RA (raw axis move, declination untouched) for shot 2..."
    )

    ra_hours_2 = _rotate_ra_axis_by(mount, step_deg, on_step=_step)
    _step("Settling before next exposure...")
    time.sleep(settle_seconds)
    _step("Shot 2 of 3: imaging...")
    point2 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=1, on_step=_step)
    _step(
        f"Shot 2 solved at RA {point2['ra_deg']:.2f}°/Dec {point2['dec_deg']:.2f}°. "
        f"Rotating another {step_deg:.0f}° in RA for shot 3..."
    )

    ra_hours_3 = _rotate_ra_axis_by(mount, step_deg, on_step=_step)
    _step("Settling before next exposure...")
    time.sleep(settle_seconds)
    _step("Shot 3 of 3: imaging...")
    point3 = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN", frame_index=2, on_step=_step)

    commanded_rotation_deg = _ra_delta_deg(ra_hours_1, ra_hours_3)

    _step(
        f"Shot 3 solved at RA {point3['ra_deg']:.2f}°/Dec {point3['dec_deg']:.2f}°. "
        f"Computing polar axis error from the {2 * step_deg:.0f}° baseline between shots 1 and 3..."
    )
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
    adjustment = describe_adjustment(result["az_error_arcmin"], result["alt_error_arcmin"])
    logger.info("Polar alignment calibration: %s", adjustment["text"])
    _step(f"Calibration done: {adjustment['text']} Mount stays right here now -- entering monitor phase.")

    return {
        **result,
        "description": adjustment["text"],
        "az_move_direction": adjustment["az_move_direction"],
        "az_move_amount": adjustment["az_move_amount"],
        "alt_move_direction": adjustment["alt_move_direction"],
        "alt_move_amount": adjustment["alt_move_amount"],
        "commanded_rotation_deg": commanded_rotation_deg,
        "last_heading": {"ra_deg": point3["ra_deg"], "dec_deg": point3["dec_deg"]},
    }


def monitor_once(exposure_seconds: float = 5.0, on_step: Any = None) -> dict[str, Any]:
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
    point = _capture_and_solve(mount, camera, exposure_seconds, "POLAR_ALIGN_MONITOR", frame_index=0, on_step=on_step)
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
    "message": None,
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


def _run_loop(exposure_seconds: float, step_deg: float) -> None:
    def _set_message(msg: str) -> None:
        with _lock:
            _state["message"] = msg
        logger.info("Polar alignment: %s", msg)

    try:
        with _lock:
            _state["phase"] = "calibrating"
        calibration = calibrate(exposure_seconds=exposure_seconds, step_deg=step_deg, on_step=_set_message)
        with _lock:
            _state["calibration"] = calibration
            _state["phase"] = "monitoring"
    except Exception as exc:
        logger.warning("Polar alignment calibration failed: %s", exc)
        with _lock:
            _state["error"] = str(exc)
            _state["running"] = False
            _state["phase"] = "idle"
            _state["message"] = f"Calibration failed: {exc}"
        return

    baseline = None
    count = 0
    _set_message("Monitor phase: mount will not move again. Capturing baseline image...")
    while not _stop_flag.is_set():
        try:
            point = monitor_once(exposure_seconds=exposure_seconds, on_step=_set_message)
            with _lock:
                if baseline is None:
                    baseline = point
                    _state["monitor_baseline"] = baseline
                _state["monitor_latest"] = point
                drift = _separation_arcsec(baseline["ra_deg"], baseline["dec_deg"], point["ra_deg"], point["dec_deg"])
                _state["monitor_drift_arcsec"] = drift
                count += 1
                _state["monitor_count"] = count
                _state["error"] = None
            if count == 1:
                _set_message("Baseline captured. Adjust the mount's azimuth/altitude bolts now and watch the drift below.")
            else:
                _set_message(f"Monitoring (reading {count}): drift {drift:.1f}\" from baseline since you started adjusting.")
        except Exception as exc:
            logger.warning("Polar alignment monitor capture failed: %s", exc)
            with _lock:
                _state["error"] = str(exc)
                _state["message"] = f"Monitor capture failed, retrying in 5s: {exc}"
            _stop_flag.wait(5.0)

    with _lock:
        _state["running"] = False
        _state["phase"] = "idle"
        _state["message"] = "Stopped."


def start_continuous_polar_alignment(exposure_seconds: float = 5.0, step_deg: float = 20.0) -> None:
    global _thread
    with _lock:
        if _state["running"]:
            raise PolarAlignmentError("Polar alignment is already running")
        _state["running"] = True
        _state["phase"] = "calibrating"
        _state["message"] = "Starting calibration..."
        _state["calibration"] = None
        _state["monitor_baseline"] = None
        _state["monitor_latest"] = None
        _state["monitor_drift_arcsec"] = None
        _state["monitor_count"] = 0
        _state["error"] = None
    _stop_flag.clear()
    _thread = threading.Thread(
        target=_run_loop,
        args=(exposure_seconds, step_deg),
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
