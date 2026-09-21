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
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image
from sqlalchemy.orm import Session
from sqlmodel import select

from app.core.config import settings
from app.db.session import get_session_dep
from app.models.capture import CaptureLog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hardware", tags=["hardware"])


_NINA_UNREACHABLE = object()  # sentinel: cache a failed attempt too


def _nina_status_cached(cache: dict[str, Any]) -> dict[str, Any]:
    """NinaBridgeService.get_status() covers telescope+camera+focuser in
    one call -- fetch (or fail) it at most once per request even if
    multiple subsystems are on the nina backend. Confirmed live: without
    caching the FAILURE too, a NINA-unreachable request took ~15s (three
    independent 5s timeouts for mount+camera+guiding each retrying the
    same failing connection) instead of one."""
    if "nina" not in cache:
        from app.services.nina_client import NinaBridgeService

        try:
            # Short timeout -- NinaBridgeService defaults to 300s (fine
            # for actual exposures, unacceptable for a quick status poll).
            cache["nina"] = NinaBridgeService(timeout=3.0).get_status()
        except Exception as exc:
            cache["nina"] = _NINA_UNREACHABLE
            cache["nina_error"] = str(exc)
    if cache["nina"] is _NINA_UNREACHABLE:
        raise RuntimeError(cache["nina_error"])
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
        # Reuses the shared cached NINA reachability check (and its
        # failure) rather than making an independent, redundant 5s-timeout
        # attempt when NINA is already known unreachable this request --
        # see _nina_status_cached's docstring for why this matters.
        _nina_status_cached(cache)
        from app.services.nina_client import NinaBridgeService

        guider = NinaBridgeService(timeout=3.0).guider_info()
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


def _latest_capture(db: Session) -> CaptureLog | None:
    # Skip capture records with no real file -- created when a capture
    # started but was interrupted before a FITS path was ever assigned
    # (confirmed live: this is a real, normal state to encounter, not a
    # bug -- e.g. a session stopped mid-exposure during testing).
    return db.exec(
        select(CaptureLog)
        .where(CaptureLog.path != "")
        .order_by(CaptureLog.started_at.desc())
    ).first()


@router.get("/camera/latest")
def latest_capture_info(db: Session = Depends(get_session_dep)) -> dict[str, Any]:
    """Metadata for the most recent capture, for the frontend to show
    alongside the preview image (target, timestamp, exposure, solve
    status) without re-parsing the FITS file itself."""
    capture = _latest_capture(db)
    if capture is None:
        return {"available": False}
    return {
        "available": True,
        "capture_id": capture.id,
        "target": capture.target,
        "started_at": capture.started_at.isoformat(),
        "exposure_seconds": capture.exposure_seconds,
        "filter_name": capture.filter_name,
        "has_wcs": capture.has_wcs,
        "error_message": capture.error_message,
    }


@router.get("/camera/preview")
def camera_preview(db: Session = Depends(get_session_dep)) -> Response:
    """A stretched, downscaled JPEG preview of the most recent capture --
    browsers can't display raw 16-bit FITS directly. Renders the raw
    sensor data as grayscale (no debayering) -- good enough to confirm
    something is in frame and roughly in focus; a proper color preview
    is a later enhancement, not needed for this to be useful."""
    capture = _latest_capture(db)
    if capture is None or not Path(capture.path).exists():
        raise HTTPException(status_code=404, detail="No capture available")

    try:
        data = fits.getdata(capture.path).astype(float)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read FITS data: {exc}") from exc

    lo, hi = np.percentile(data, [1.0, 99.5])
    if hi <= lo:
        hi = lo + 1.0
    stretched = np.clip((data - lo) / (hi - lo), 0.0, 1.0)
    img8 = (stretched * 255).astype(np.uint8)

    image = Image.fromarray(img8, mode="L")
    image.thumbnail((1200, 1200))

    buf = BytesIO()
    image.save(buf, format="JPEG", quality=85)
    return Response(content=buf.getvalue(), media_type="image/jpeg")


def _current_altaz(ra_deg: float, dec_deg: float) -> dict[str, float]:
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    import astropy.units as u
    from app.core.site_config import load_site_config

    site_config = load_site_config()
    location = EarthLocation(
        lat=site_config.latitude * u.deg,
        lon=site_config.longitude * u.deg,
        height=site_config.altitude_m * u.m,
    )
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    altaz = coord.transform_to(AltAz(obstime=Time.now(), location=location))
    return {"alt_deg": float(altaz.alt.deg), "az_deg": float(altaz.az.deg)}


@router.get("/sky")
def sky_view(db: Session = Depends(get_session_dep)) -> dict[str, Any]:
    """Everything the Sky View page needs to render one live polar plot:
    current mount pointing, the horizon obstruction mask, the active
    target's position (if any), and sun/moon for context."""
    from astropy.coordinates import AltAz, EarthLocation, get_body
    from astropy.time import Time
    import astropy.units as u
    from app.core.site_config import load_site_config
    from app.services.observability import HorizonMask
    from app.models.session import ObservingSession

    site_config = load_site_config()
    location = EarthLocation(
        lat=site_config.latitude * u.deg,
        lon=site_config.longitude * u.deg,
        height=site_config.altitude_m * u.m,
    )
    now = Time.now()

    result: dict[str, Any] = {}

    cache: dict[str, Any] = {}
    mount = _mount_status(cache)
    if mount.get("reachable") and mount.get("ra_deg") is not None and mount.get("dec_deg") is not None:
        try:
            result["mount"] = _current_altaz(mount["ra_deg"], mount["dec_deg"])
        except Exception as exc:
            logger.debug("Could not compute mount alt/az: %s", exc)
            result["mount"] = None
    else:
        result["mount"] = None

    horizon_mask = HorizonMask.from_path(
        site_config.horizon_mask.source if site_config.horizon_mask else None
    )
    if horizon_mask is not None:
        sample_az = list(range(0, 360, 5))
        sample_alt = horizon_mask.limit_for(np.array(sample_az, dtype=float))
        result["horizon"] = [
            {"az_deg": az, "alt_deg": float(alt)} for az, alt in zip(sample_az, sample_alt)
        ]
    else:
        result["horizon"] = []

    try:
        sun_altaz = get_body("sun", now, location=location).transform_to(
            AltAz(obstime=now, location=location)
        )
        result["sun"] = {"alt_deg": float(sun_altaz.alt.deg), "az_deg": float(sun_altaz.az.deg)}
        moon_altaz = get_body("moon", now, location=location).transform_to(
            AltAz(obstime=now, location=location)
        )
        result["moon"] = {"alt_deg": float(moon_altaz.alt.deg), "az_deg": float(moon_altaz.az.deg)}
    except Exception as exc:
        logger.debug("Could not compute sun/moon position: %s", exc)
        result["sun"] = None
        result["moon"] = None

    active_session = db.exec(
        select(ObservingSession).where(ObservingSession.status == "active")
    ).first()
    result["target"] = None
    if active_session and active_session.selected_target:
        capture = db.exec(
            select(CaptureLog)
            .where(CaptureLog.target == active_session.selected_target)
            .where(CaptureLog.predicted_ra_deg.is_not(None))
            .order_by(CaptureLog.started_at.desc())
        ).first()
        if capture and capture.predicted_ra_deg is not None and capture.predicted_dec_deg is not None:
            try:
                altaz = _current_altaz(capture.predicted_ra_deg, capture.predicted_dec_deg)
                result["target"] = {"name": active_session.selected_target, **altaz}
            except Exception as exc:
                logger.debug("Could not compute target alt/az: %s", exc)

    return result


@router.get("/guiding/history")
def guiding_history() -> dict[str, Any]:
    """Recent GuideStep telemetry from the background PHD2 listener (see
    guide_telemetry.py) -- empty when guiding_backend != "phd2" or PHD2
    isn't currently connected, not an error."""
    from app.services.guide_telemetry import get_guide_history

    return {"steps": get_guide_history()}


@router.post("/polar-align/run")
def run_polar_alignment_measurement(
    exposure_seconds: float = 5.0, step_deg: float = 20.0
) -> dict[str, Any]:
    """Run one all-sky polar alignment calibration (see
    all_sky_polar_align.py): three shots step_deg apart in RA (same
    declination), reporting the axis error from the widest-separated
    pair. Two slews total -- leaves the mount at the final pointing, no
    further slewing. A real, deliberate mount/camera action -- only ever
    triggered by an explicit request, never automatically."""
    from app.services.all_sky_polar_align import calibrate
    from app.services.polar_alignment import PolarAlignmentError
    from app.services.alpaca_camera_client import AlpacaError as CameraAlpacaError
    from app.services.alpaca_mount_client import AlpacaError as MountAlpacaError

    try:
        return calibrate(exposure_seconds=exposure_seconds, step_deg=step_deg)
    except (PolarAlignmentError, CameraAlpacaError, MountAlpacaError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/polar-align/start")
def start_continuous_polar_alignment(
    exposure_seconds: float = 5.0, step_deg: float = 20.0
) -> dict[str, Any]:
    """Start a polar alignment session: one calibration (two slews, three
    shots step_deg apart in RA) followed by a monitor phase that performs
    NO further slewing -- just repeatedly captures and solves at the
    final fixed pointing while you physically adjust the mount's
    azimuth/altitude bolts, reporting how far the star field has drifted
    from the first monitor reading. Real, deliberate mount/camera action
    -- only starts on explicit request."""
    from app.services.all_sky_polar_align import start_continuous_polar_alignment as _start
    from app.services.polar_alignment import PolarAlignmentError

    try:
        _start(exposure_seconds=exposure_seconds, step_deg=step_deg)
        return {"started": True}
    except PolarAlignmentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/polar-align/stop")
def stop_continuous_polar_alignment() -> dict[str, Any]:
    """Stop the polar alignment session. Only affects the monitor
    phase's repeated capture+solve loop -- there is no slewing to stop,
    the mount is left exactly where the calibration phase last put it."""
    from app.services.all_sky_polar_align import stop_continuous_polar_alignment as _stop

    _stop()
    return {"stopped": True}


@router.get("/polar-align/state")
def polar_alignment_state() -> dict[str, Any]:
    from app.services.all_sky_polar_align import get_polar_align_state

    return get_polar_align_state()
