"""
Sequential capture service with confirmation loop.

This service orchestrates the entire capture flow synchronously:
1. Query Horizons for fresh ephemeris
2. Confirmation loop (up to 3 attempts):
   - Slew to predicted position
   - Capture short confirmation image
   - Plate solve
   - Check centering
   - Re-slew if needed
3. Capture main science exposure
4. Plate solve main image
5. Detect sources and associate with predicted position

All operations are synchronous and traceable.
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import astropy.units as u
from astropy.coordinates import FK5, SkyCoord
from astropy.time import Time

from sqlalchemy.orm import Session
from sqlmodel import select

from app.core.config import settings
from app.core.site_config import load_site_config
from app.models.analysis import CandidateAssociation
from app.models.neocp import NeoCandidate, NeoEphemeris
from app.models.capture import CaptureLog
from app.services.analysis import AnalysisService
from app.services.file_poller import poll_for_fits_file, wait_for_file_size_stable
from app.services.scout_client import ScoutClient
from app.services.horizons_client import HorizonsClient
from app.services.nina_client import NinaBridgeService
from app.services.solver import SolveError, solve_fits

logger = logging.getLogger(__name__)


class SequentialCaptureService:
    """
    Orchestrates sequential capture with confirmation loop.
    """

    def __init__(
        self,
        db: Session,
        nina_client: Optional[NinaBridgeService] = None,
        scout_client: Optional[ScoutClient] = None,
        analysis: Optional[AnalysisService] = None,
    ):
        self.db = db
        self.nina = nina_client or NinaBridgeService()

        # Initialize Scout client with site configuration
        if scout_client:
            self.scout = scout_client
        else:
            site_config = load_site_config()
            self.scout = ScoutClient(
                obs_code=site_config.station_code,
                timeout=settings.scout_timeout,
                base_url=settings.scout_api_url,
            )
        self.horizons = HorizonsClient(
            site_lat=site_config.latitude,
            site_lon=site_config.longitude,
            site_alt_m=site_config.altitude_m,
            timeout=settings.horizons_timeout,
        )

        self.analysis = analysis or AnalysisService(db)

    @staticmethod
    def _to_mount_coords(ra_deg: float, dec_deg: float) -> tuple[float, float]:
        """Convert ICRS/J2000 coords to the mount frame used by NINA."""
        frame = settings.nina_slew_frame.strip().lower()
        if frame in ("jnow", "fk5", "apparent"):
            try:
                coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
                jnow = coord.transform_to(FK5(equinox=Time.now()))
                return float(jnow.ra.deg), float(jnow.dec.deg)
            except Exception as exc:
                logger.warning("Failed to convert coords to %s; using ICRS: %s", frame, exc)
        return ra_deg, dec_deg

    @staticmethod
    def _format_ra_dec(ra_deg: float, dec_deg: float) -> tuple[str, str]:
        coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        ra_hms = coord.ra.to_string(unit=u.hour, sep=":", pad=True, precision=0)

        dms = coord.dec.signed_dms
        sign = "-" if dms.sign < 0 else ""
        deg = int(abs(dms.d))
        minute = int(abs(dms.m))
        second = int(round(abs(dms.s)))
        if second == 60:
            second = 0
            minute += 1
        if minute == 60:
            minute = 0
            deg += 1
        dec_dms = f"{sign}{deg:02d}° {minute:02d}' {second:02d}\""
        return ra_hms, dec_dms

    @staticmethod
    def _max_target_altitude() -> float | None:
        max_alt = settings.max_target_altitude_deg
        if max_alt is None:
            return None
        if max_alt >= 90.0:
            return None
        return float(max_alt)

    @staticmethod
    def _jnow_to_icrs(ra_deg: float, dec_deg: float) -> tuple[float, float]:
        coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame=FK5(equinox=Time.now()))
        icrs = coord.icrs
        return float(icrs.ra.deg), float(icrs.dec.deg)

    @staticmethod
    def _to_icrs_from_epoch(
        ra_deg: float,
        dec_deg: float,
        epoch: str | float | None,
    ) -> tuple[float, float]:
        if epoch is None:
            return ra_deg, dec_deg
        epoch_str = str(epoch).strip().upper()
        if epoch_str in ("JNOW", "NOW"):
            return SequentialCaptureService._jnow_to_icrs(ra_deg, dec_deg)
        return ra_deg, dec_deg

    def _solve_with_progressive_radius(
        self,
        *,
        fits_path: str | Path,
        ra_hint: float,
        dec_hint: float,
        base_radius_deg: float,
        downsample: int | None,
        sigma: float | None,
        scale_low_arcsec: float | None,
        scale_high_arcsec: float | None,
        max_radius_deg: float = 2.0,
        timeout_seconds: float | None = None,
        radius_steps: list[float] | None = None,
        timeout_steps: list[int | None] | None = None,
    ) -> tuple[dict[str, Any], float]:
        if radius_steps:
            radii = [float(step) for step in radius_steps]
        else:
            radii = []
            radius = max(0.1, float(base_radius_deg))
            cap = max(radius, float(max_radius_deg))
            radii.append(radius)
            for factor in (1.5, 2.0):
                next_radius = radius * factor
                if next_radius < cap:
                    radii.append(next_radius)
            while radii[-1] < cap:
                next_radius = min(cap, radii[-1] * 2.0)
                if next_radius <= radii[-1]:
                    break
                radii.append(next_radius)
        if timeout_steps:
            timeouts = list(timeout_steps)
        else:
            base_timeout = timeout_seconds or settings.astrometry_solve_timeout
            timeouts = []
            if base_timeout:
                factors = [0.5, 0.75, 1.0]
                for idx in range(len(radii)):
                    factor = factors[idx] if idx < len(factors) else 1.0
                    timeouts.append(max(30, int(base_timeout * factor)))
            else:
                timeouts = [None for _ in radii]
        last_exc: Exception | None = None
        for attempt_radius, attempt_timeout in zip(radii, timeouts, strict=False):
            try:
                logger.info(
                    "Attempting plate solve with radius %.2f deg (timeout=%ss)",
                    attempt_radius,
                    attempt_timeout if attempt_timeout is not None else "default",
                )
                result = solve_fits(
                    fits_path=fits_path,
                    ra_hint=ra_hint,
                    dec_hint=dec_hint,
                    radius_deg=attempt_radius,
                    downsample=downsample,
                    sigma=sigma,
                    scale_low_arcsec=scale_low_arcsec,
                    scale_high_arcsec=scale_high_arcsec,
                    timeout=attempt_timeout,
                )
                return result, attempt_radius
            except SolveError as exc:
                last_exc = exc
                logger.warning(
                    "Plate solve failed with radius %.2f deg: %s",
                    attempt_radius,
                    exc,
                )
        if last_exc:
            raise last_exc
        raise SolveError("Plate solve failed with no attempts")

    def capture_with_confirmation(
        self,
        target_name: str,
        candidate_id: str,
        exposure_seconds: float,
        filter_name: str = "L",
        binning: int = 1,
        confirmation_exposure_seconds: float = 5.0,
        confirmation_binning: int = 1,
        confirmation_max_attempts: int = 3,
        centering_tolerance_arcsec: float | None = None,
    ) -> dict[str, Any]:
        """
        Capture a single image with confirmation loop and process synchronously.

        Args:
            target_name: Name of the target (e.g., "ZTF109i")
            candidate_id: MPC designation for Horizons query
            exposure_seconds: Main exposure duration
            filter_name: Filter for main exposure
            binning: Binning for main exposure
            confirmation_exposure_seconds: Confirmation exposure duration
            confirmation_binning: Confirmation binning
            confirmation_max_attempts: Max re-centering attempts
            centering_tolerance_arcsec: Max allowed offset for confirmation (arcsec). If None,
                derive from camera FOV (25% of smaller dimension).

        Returns:
            Dictionary with:
                success: bool
                capture_id: int | None
                fits_path: str | None
                solved: bool
                association_id: int | None
                error: str | None
                confirmation_attempts: int
                predicted_ra_deg: float | None
                predicted_dec_deg: float | None
                solved_ra_deg: float | None
                solved_dec_deg: float | None
        """
        logger.info(
            f"Starting capture for {target_name} (candidate_id={candidate_id}), "
            f"exposure={exposure_seconds}s, filter={filter_name}, binning={binning}"
        )

        # Step 1: Get fresh ephemeris from Horizons (fallback to Scout if needed)
        try:
            now = datetime.utcnow()
            window_minutes = settings.horizons_session_window_minutes
            step_minutes = settings.horizons_session_step_minutes
            rows = self.horizons.fetch_ephemeris(
                target_designation=candidate_id,
                start_time=now,
                stop_time=now + timedelta(minutes=window_minutes),
                step_minutes=step_minutes,
            )
            if not rows:
                raise RuntimeError("Horizons returned no ephemeris rows")
            candidate = self.db.exec(
                select(NeoCandidate).where(NeoCandidate.id == candidate_id)
            ).first()
            if candidate:
                self._upsert_ephemeris_rows(candidate, rows, source="HORIZONS")
                logger.info(
                    "Stored %d Horizons ephemeris points for %s (window=%dm step=%dm)",
                    len(rows),
                    candidate_id,
                    window_minutes,
                    step_minutes,
                )
            ephemeris = min(rows, key=lambda row: abs((row["epoch"] - now).total_seconds()))
            predicted_ra = ephemeris["ra_deg"]
            predicted_dec = ephemeris["dec_deg"]
            rate_arcsec_per_min = self._estimate_rate_arcsec_per_min(ephemeris)
            predicted_hms, predicted_dms = self._format_ra_dec(predicted_ra, predicted_dec)
            logger.info(
                "Horizons ephemeris: RA=%.6f (%s), Dec=%.6f (%s)",
                predicted_ra,
                predicted_hms,
                predicted_dec,
                predicted_dms,
            )
            max_altitude = self._max_target_altitude()
            elevation = ephemeris.get("elevation_deg")
            if (
                max_altitude is not None
                and elevation is not None
                and float(elevation) >= max_altitude
            ):
                logger.warning(
                    "Target %s too close to zenith (elevation %.1f° >= %.1f°); skipping.",
                    candidate_id,
                    float(elevation),
                    max_altitude,
                )
                return {
                    "success": False,
                    "error": (
                        f"Target elevation {float(elevation):.1f}° exceeds limit "
                        f"{max_altitude:.1f}°"
                    ),
                    "confirmation_attempts": 0,
                }
        except Exception as e:
            logger.warning("Horizons query failed for %s: %s", candidate_id, e)
            try:
                ephemeris = self.scout.get_current_position(candidate_id)
                predicted_ra = ephemeris["ra_deg"]
                predicted_dec = ephemeris["dec_deg"]
                rate_arcsec_per_min = self._estimate_rate_arcsec_per_min(ephemeris)
                predicted_hms, predicted_dms = self._format_ra_dec(predicted_ra, predicted_dec)
                logger.info(
                    "Scout ephemeris: RA=%.6f (%s), Dec=%.6f (%s)",
                    predicted_ra,
                    predicted_hms,
                    predicted_dec,
                    predicted_dms,
                )
                max_altitude = self._max_target_altitude()
                elevation = ephemeris.get("elevation_deg")
                if (
                    max_altitude is not None
                    and elevation is not None
                    and float(elevation) >= max_altitude
                ):
                    logger.warning(
                        "Target %s too close to zenith (elevation %.1f° >= %.1f°); skipping.",
                        candidate_id,
                        float(elevation),
                        max_altitude,
                    )
                    return {
                        "success": False,
                        "error": (
                            f"Target elevation {float(elevation):.1f}° exceeds limit "
                            f"{max_altitude:.1f}°"
                        ),
                        "confirmation_attempts": 0,
                    }
            except Exception as scout_exc:
                logger.error("Failed to query ephemerides for %s: %s", candidate_id, scout_exc)
                return {
                    "success": False,
                    "error": f"Ephemeris query failed: {scout_exc}",
                    "confirmation_attempts": 0,
                }

        # Step 2: Confirmation loop (up to 3 attempts)
        tolerances = self._compute_tolerances(
            rate_arcsec_per_min=rate_arcsec_per_min,
            center_override_arcsec=centering_tolerance_arcsec,
        )
        center_arcsec = tolerances["center_arcsec"]
        acquire_arcsec = tolerances["acquire_arcsec"]
        pixel_scale = None
        if settings.astrometry_pixel_scale_arcsec:
            pixel_scale = settings.astrometry_pixel_scale_arcsec
        elif settings.camera_pixel_size_um and settings.telescope_focal_length_mm:
            pixel_scale = 206.265 * settings.camera_pixel_size_um / settings.telescope_focal_length_mm
        scale_low, scale_high = self._compute_scale_bounds(
            pixel_scale,
            settings.confirmation_scale_low_arcsec,
            settings.confirmation_scale_high_arcsec,
        )
        confirmation_attempts = 0
        final_ra = predicted_ra
        final_dec = predicted_dec
        confirmation_success = False
        confirmation_result: dict[str, Any] | None = None

        # Confirmation capture/solve disabled temporarily; keep stubs for later re-enable.
        confirmation_bypass = True
        logger.info("Confirmation capture skipped (temporary bypass).")
        try:
            slew_ra, slew_dec = self._to_mount_coords(final_ra, final_dec)
            slew_hms, slew_dms = self._format_ra_dec(slew_ra, slew_dec)
            if settings.nina_slew_frame.strip().lower() != "icrs":
                icrs_hms, icrs_dms = self._format_ra_dec(final_ra, final_dec)
                logger.info(
                    "Slewing to RA=%.6f (%s), Dec=%.6f (%s) (mount frame=%s from ICRS %.6f (%s), %.6f (%s))",
                    slew_ra,
                    slew_hms,
                    slew_dec,
                    slew_dms,
                    settings.nina_slew_frame,
                    final_ra,
                    icrs_hms,
                    final_dec,
                    icrs_dms,
                )
            else:
                logger.info(
                    "Slewing to RA=%.6f (%s), Dec=%.6f (%s)",
                    slew_ra,
                    slew_hms,
                    slew_dec,
                    slew_dms,
                )
            self.nina.slew(slew_ra, slew_dec)
            self.nina.wait_for_mount_ready(timeout=60.0)
            try:
                self.nina.start_guiding()
                logger.info("Guiding started.")
            except Exception as exc:
                logger.warning("Failed to start guiding: %s", exc)
            logger.info("Slew complete; proceeding with science exposure.")
        except Exception as e:
            logger.error("Slew failed: %s", e)
            return {
                "success": False,
                "error": f"Slew failed: {e}",
                "confirmation_attempts": 0,
            }

        confirmation_success = True
        confirmation_result = {
            "success": True,
            "capture_id": None,
            "fits_path": None,
            "solved": False,
            "association_id": None,
            "confirmation_attempts": 0,
            "predicted_ra_deg": predicted_ra,
            "predicted_dec_deg": predicted_dec,
            "confirmation_only": False,
        }

        if confirmation_success and settings.test_mode_slew_only and not confirmation_bypass:
            try:
                science_capture_start = time.time()
                logger.info("Capturing science image: %s", target_name)
                self.nina.wait_for_camera_idle(timeout=60.0)
                self.nina.start_exposure(
                    filter_name=filter_name,
                    binning=binning,
                    exposure_seconds=exposure_seconds,
                    target=target_name,
                    request_solve=False,
                )
                self.nina.wait_for_camera_idle(timeout=exposure_seconds + 30.0)
            except Exception as e:
                logger.error("Science capture failed: %s", e)
                return {
                    "success": False,
                    "error": f"Science capture failed: {e}",
                    "confirmation_attempts": confirmation_attempts,
                }

            exclude_paths = {
                row[0]
                for row in self.db.exec(
                    select(CaptureLog.path)
                    .where(CaptureLog.target == target_name)
                    .where(CaptureLog.path != "")
                    .order_by(CaptureLog.started_at.desc())
                    .limit(5)
                )
                if row[0]
            }
            science_path = poll_for_fits_file(
                target_name=target_name,
                fits_directory=settings.nina_images_path,
                timeout=exposure_seconds + 60.0,
                min_mtime=science_capture_start - 1.0,
                exclude_paths=exclude_paths or None,
            )
            if not science_path:
                logger.error("Science FITS file not found")
                return {
                    "success": False,
                    "error": "Science image not created",
                    "confirmation_attempts": confirmation_attempts,
                }
            if not wait_for_file_size_stable(science_path, stable_duration=1.0, timeout=10.0):
                logger.warning("Science file size did not stabilize, continuing anyway")

            logger.info("Science exposure saved: %s", science_path)
            try:
                solve_base_radius = float(settings.confirmation_solve_radius_deg)
                science_scale_low, science_scale_high = scale_low, scale_high
                if pixel_scale:
                    science_scale_low = pixel_scale * 0.9
                    science_scale_high = pixel_scale * 1.1
                solve_result, _ = self._solve_with_progressive_radius(
                    fits_path=science_path,
                    ra_hint=predicted_ra,
                    dec_hint=predicted_dec,
                    base_radius_deg=solve_base_radius,
                    downsample=settings.confirmation_solve_downsample,
                    sigma=settings.confirmation_solve_sigma,
                    scale_low_arcsec=science_scale_low,
                    scale_high_arcsec=science_scale_high,
                    max_radius_deg=1.2,
                    timeout_seconds=settings.astrometry_solve_timeout,
                    radius_steps=[0.2, 0.3, 0.4],
                    timeout_steps=[45, 60, 90],
                )
                solved_raw_ra = solve_result["solution"]["ra_deg"]
                solved_raw_dec = solve_result["solution"]["dec_deg"]
                solved_epoch = solve_result["solution"].get("epoch")
                solved_ra, solved_dec = self._to_icrs_from_epoch(
                    solved_raw_ra,
                    solved_raw_dec,
                    solved_epoch,
                )
                solved_pixscale = solve_result["solution"].get("pixscale")
                if solved_pixscale and science_scale_low and science_scale_high:
                    if not (science_scale_low <= solved_pixscale <= science_scale_high):
                        raise RuntimeError("Science plate solve pixel scale out of range")
                hint_sep_arcsec = self._calculate_separation_arcsec(
                    predicted_ra, predicted_dec, solved_ra, solved_dec
                )
                max_hint_arcsec = solve_base_radius * 3600.0
                if hint_sep_arcsec > max_hint_arcsec:
                    raise RuntimeError("Science plate solve outside hint radius")
                solved_hms, solved_dms = self._format_ra_dec(solved_ra, solved_dec)
                logger.info(
                    "Science plate solve success (ICRS): RA=%.6f (%s), Dec=%.6f (%s)",
                    solved_ra,
                    solved_hms,
                    solved_dec,
                    solved_dms,
                )
                if confirmation_result:
                    confirmation_result["fits_path"] = str(science_path)
                logger.info("Science plate solve success; test mode stopping session.")
                return confirmation_result or {"success": True, "confirmation_only": True}
            except Exception as e:
                logger.error("Science plate solve failed: %s", e)
                return {
                    "success": False,
                    "error": f"Science plate solve failed: {e}",
                    "confirmation_attempts": confirmation_attempts,
                }

        # Step 3: Create capture record for main exposure
        capture = CaptureLog(
            kind="science",
            target=target_name,
            path="",
            started_at=datetime.utcnow(),
            predicted_ra_deg=final_ra,
            predicted_dec_deg=final_dec,
            filter_name=filter_name,
            binning=binning,
            exposure_seconds=exposure_seconds,
        )
        self.db.add(capture)
        self.db.commit()
        self.db.refresh(capture)

        logger.info(f"Created capture record: id={capture.id}")

        # Step 4: Take main science exposure
        try:
            main_capture_start = time.time()
            logger.info(f"Capturing main science image: {target_name}")
            self.nina.wait_for_camera_idle(timeout=60.0)
            self.nina.start_exposure(
                filter_name=filter_name,
                binning=binning,
                exposure_seconds=exposure_seconds,
                target=target_name,
                request_solve=False,  # Never rely on NINA solving
            )
            self.nina.wait_for_camera_idle(timeout=exposure_seconds + 30.0)
        except Exception as e:
            logger.error(f"Main capture failed: {e}")
            capture.error_message = f"Capture failed: {e}"
            self.db.commit()
            return {
                "success": False,
                "capture_id": capture.id,
                "error": f"Main capture failed: {e}",
                "confirmation_attempts": confirmation_attempts,
            }

        # Step 5: Wait for main FITS file
        exclude_paths = {
            row[0]
            for row in self.db.exec(
                select(CaptureLog.path)
                .where(CaptureLog.target == target_name)
                .where(CaptureLog.path != "")
                .order_by(CaptureLog.started_at.desc())
                .limit(5)
            )
            if row[0]
        }
        fits_path = poll_for_fits_file(
            target_name=target_name,
            fits_directory=settings.nina_images_path,
            timeout=exposure_seconds + 60.0,  # Exposure time + buffer
            min_mtime=main_capture_start - 1.0,
            exclude_paths=exclude_paths or None,
        )
        if not fits_path:
            logger.error("Main FITS file not found")
            capture.error_message = "FITS file not created"
            self.db.commit()
            return {
                "success": False,
                "capture_id": capture.id,
                "error": "Science image not created",
                "confirmation_attempts": confirmation_attempts,
            }

        # Wait for file write to complete
        if not wait_for_file_size_stable(fits_path, stable_duration=2.0, timeout=30.0):
            logger.warning("Main file size did not stabilize, continuing anyway")

        # Update capture with path
        capture.path = str(fits_path)
        self.db.commit()

        logger.info(f"Main FITS file saved: {fits_path}")

        # Step 6: Plate solve main image
        try:
            logger.info(f"Solving main science image: {fits_path}")
            scale_low = None
            scale_high = None
            if settings.astrometry_pixel_scale_arcsec:
                scale_low = settings.astrometry_pixel_scale_arcsec * 0.9
                scale_high = settings.astrometry_pixel_scale_arcsec * 1.1
            solve_result = solve_fits(
                fits_path=fits_path,
                ra_hint=final_ra,
                dec_hint=final_dec,
                scale_low_arcsec=scale_low,
                scale_high_arcsec=scale_high,
            )
            solved_raw_ra = solve_result["solution"]["ra_deg"]
            solved_raw_dec = solve_result["solution"]["dec_deg"]
            solved_epoch = solve_result["solution"].get("epoch")
            solved_ra, solved_dec = self._to_icrs_from_epoch(
                solved_raw_ra,
                solved_raw_dec,
                solved_epoch,
            )
            max_sep = settings.astrometry_max_hint_separation_arcsec
            if max_sep is not None:
                sep_arcsec = self._calculate_separation_arcsec(
                    final_ra,
                    final_dec,
                    solved_ra,
                    solved_dec,
                )
                try:
                    mount_info = self.nina.mount_info()
                    mount_ra = float(mount_info.get("ra_deg")) if mount_info.get("ra_deg") is not None else None
                    mount_dec = float(mount_info.get("dec_deg")) if mount_info.get("dec_deg") is not None else None
                    mount_icrs_ra = None
                    mount_icrs_dec = None
                    if mount_ra is not None and mount_dec is not None:
                        mount_icrs_ra, mount_icrs_dec = self._jnow_to_icrs(mount_ra, mount_dec)
                    sep_pred_mount = (
                        self._calculate_separation_arcsec(final_ra, final_dec, mount_icrs_ra, mount_icrs_dec)
                        if mount_icrs_ra is not None and mount_icrs_dec is not None
                        else None
                    )
                    sep_mount_solved = (
                        self._calculate_separation_arcsec(mount_icrs_ra, mount_icrs_dec, solved_ra, solved_dec)
                        if mount_icrs_ra is not None and mount_icrs_dec is not None
                        else None
                    )
                    logger.info(
                        "Science solve offsets: predicted=(%.6f, %.6f) mount_icrs=(%s, %s) solved=(%.6f, %.6f) sep_pred_solved=%.1f\" sep_pred_mount=%s sep_mount_solved=%s",
                        final_ra,
                        final_dec,
                        f"{mount_icrs_ra:.6f}" if mount_icrs_ra is not None else "n/a",
                        f"{mount_icrs_dec:.6f}" if mount_icrs_dec is not None else "n/a",
                        solved_ra,
                        solved_dec,
                        sep_arcsec,
                        f"{sep_pred_mount:.1f}\"" if sep_pred_mount is not None else "n/a",
                        f"{sep_mount_solved:.1f}\"" if sep_mount_solved is not None else "n/a",
                    )
                except Exception as exc:
                    logger.warning("Failed to log science solve offsets: %s", exc)
                if sep_arcsec > max_sep:
                    raise RuntimeError(
                        f"Science plate solve outside hint tolerance ({sep_arcsec:.1f}\" > {max_sep:.1f}\")"
                    )
            capture.has_wcs = True
            capture.solved_ra_deg = solved_ra
            capture.solved_dec_deg = solved_dec
            self.db.commit()
            logger.info(
                f"Main image solved: RA={capture.solved_ra_deg:.6f}, Dec={capture.solved_dec_deg:.6f}"
            )
        except Exception as e:
            logger.error(f"Main solve failed: {e}")
            capture.has_wcs = False
            capture.error_message = f"Solve failed: {e}"
            self.db.commit()
            return {
                "success": True,
                "capture_id": capture.id,
                "fits_path": str(fits_path),
                "solved": False,
                "error": str(e),
                "confirmation_attempts": confirmation_attempts,
                "predicted_ra_deg": final_ra,
                "predicted_dec_deg": final_dec,
            }

        # Step 7: Source detection & association
        try:
            logger.info("Detecting sources and associating with predicted position")
            association = self.analysis.auto_associate(
                capture=capture,
                tolerance_arcsec=10.0,
            )

            if association:
                measurement_id = None
                try:
                    measurement = self.analysis.record_measurement_from_association(
                        db=self.db,
                        capture=capture,
                        association=association,
                        exposure_seconds=exposure_seconds,
                    )
                    measurement_id = measurement.id
                except Exception as exc:
                    logger.error("Failed to store measurement for capture %s: %s", capture.id, exc)
                logger.info(
                    f"✓ Association created: id={association.id}, "
                    f"residual={association.residual_arcsec:.2f}\""
                )
                return {
                    "success": True,
                    "capture_id": capture.id,
                    "fits_path": str(fits_path),
                    "solved": True,
                    "association_id": association.id,
                    "measurement_id": measurement_id,
                    "confirmation_attempts": confirmation_attempts,
                    "predicted_ra_deg": final_ra,
                    "predicted_dec_deg": final_dec,
                    "solved_ra_deg": capture.solved_ra_deg,
                    "solved_dec_deg": capture.solved_dec_deg,
                    "residual_arcsec": association.residual_arcsec,
                }
            else:
                logger.warning("No source matched predicted position")
                return {
                    "success": True,
                    "capture_id": capture.id,
                    "fits_path": str(fits_path),
                    "solved": True,
                    "association_id": None,
                    "confirmation_attempts": confirmation_attempts,
                    "predicted_ra_deg": final_ra,
                    "predicted_dec_deg": final_dec,
                    "solved_ra_deg": capture.solved_ra_deg,
                    "solved_dec_deg": capture.solved_dec_deg,
                }
        except Exception as e:
            logger.error(f"Source detection/association failed: {e}")
            return {
                "success": True,
                "capture_id": capture.id,
                "fits_path": str(fits_path),
                "solved": True,
                "association_id": None,
                "error": f"Analysis failed: {e}",
                "confirmation_attempts": confirmation_attempts,
                "predicted_ra_deg": final_ra,
                "predicted_dec_deg": final_dec,
                "solved_ra_deg": capture.solved_ra_deg,
                "solved_dec_deg": capture.solved_dec_deg,
            }

    @staticmethod
    def _calculate_separation_arcsec(
        ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float
    ) -> float:
        """
        Calculate angular separation between two positions using spherical trigonometry.

        Args:
            ra1_deg: Right ascension of position 1 (degrees)
            dec1_deg: Declination of position 1 (degrees)
            ra2_deg: Right ascension of position 2 (degrees)
            dec2_deg: Declination of position 2 (degrees)

        Returns:
            Angular separation in arcseconds
        """
        import math

        # Convert to radians
        ra1 = math.radians(ra1_deg)
        dec1 = math.radians(dec1_deg)
        ra2 = math.radians(ra2_deg)
        dec2 = math.radians(dec2_deg)

        # Haversine formula
        dra = ra2 - ra1
        ddec = dec2 - dec1

        a = math.sin(ddec / 2) ** 2 + math.cos(dec1) * math.cos(dec2) * math.sin(dra / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))

        # Convert to arcseconds
        separation_deg = math.degrees(c)
        separation_arcsec = separation_deg * 3600.0

        return separation_arcsec

    @staticmethod
    def _estimate_rate_arcsec_per_min(ephemeris: dict[str, Any]) -> float | None:
        ra_rate = ephemeris.get("ra_rate_arcsec_min")
        dec_rate = ephemeris.get("dec_rate_arcsec_min")
        if ra_rate is None and dec_rate is None:
            return None
        if ra_rate is None:
            return abs(float(dec_rate))
        if dec_rate is None:
            return abs(float(ra_rate))
        return (float(ra_rate) ** 2 + float(dec_rate) ** 2) ** 0.5

    def _upsert_ephemeris_rows(
        self,
        candidate: NeoCandidate,
        rows: list[dict[str, Any]],
        source: str = "HORIZONS",
    ) -> None:
        if not rows:
            return
        epochs = [row["epoch"] for row in rows]
        existing = self.db.exec(
            select(NeoEphemeris)
            .where(NeoEphemeris.candidate_id == candidate.id)
            .where(NeoEphemeris.source == source)
            .where(NeoEphemeris.epoch.in_(epochs))
        ).all()
        existing_by_epoch = {eph.epoch: eph for eph in existing}
        for row in rows:
            ra_rate = row.get("ra_rate_arcsec_min")
            dec_rate = row.get("dec_rate_arcsec_min")
            rate = None
            if ra_rate is not None and dec_rate is not None:
                rate = (float(ra_rate) ** 2 + float(dec_rate) ** 2) ** 0.5
            payload = {
                "ra_deg": row["ra_deg"],
                "dec_deg": row["dec_deg"],
                "ra_rate_arcsec_min": ra_rate,
                "dec_rate_arcsec_min": dec_rate,
                "rate_arcsec_per_min": rate,
                "azimuth_deg": row.get("azimuth_deg"),
                "elevation_deg": row.get("elevation_deg"),
                "airmass": row.get("airmass"),
                "v_mag_predicted": row.get("v_mag"),
                "uncertainty_3sigma_arcsec": row.get("uncertainty_3sigma_arcsec"),
                "source": source,
            }
            existing_row = existing_by_epoch.get(row["epoch"])
            if existing_row:
                for key, value in payload.items():
                    setattr(existing_row, key, value)
                self.db.add(existing_row)
            else:
                eph = NeoEphemeris(
                    candidate_id=candidate.id,
                    trksub=candidate.trksub,
                    epoch=row["epoch"],
                    **payload,
                )
                self.db.add(eph)
        self.db.commit()

    @staticmethod
    def _compute_tolerances(
        rate_arcsec_per_min: float | None,
        center_override_arcsec: float | None,
    ) -> dict[str, float]:
        if center_override_arcsec is not None:
            return {
                "center_arcsec": float(center_override_arcsec),
                "acquire_arcsec": float(center_override_arcsec),
                "center_px": 0.0,
                "acquire_px": 0.0,
            }

        focal_length_mm = settings.telescope_focal_length_mm
        pixel_size_um = settings.camera_pixel_size_um
        res_x = settings.camera_resolution_x_px
        res_y = settings.camera_resolution_y_px
        sensor_w_mm = settings.camera_sensor_width_mm
        sensor_h_mm = settings.camera_sensor_height_mm

        if (sensor_w_mm is None or sensor_h_mm is None) and pixel_size_um and res_x and res_y:
            sensor_w_mm = (pixel_size_um * res_x) / 1000.0
            sensor_h_mm = (pixel_size_um * res_y) / 1000.0

        pixel_scale = None
        if pixel_size_um and focal_length_mm:
            pixel_scale = 206.265 * pixel_size_um / focal_length_mm
        elif sensor_w_mm and sensor_h_mm and focal_length_mm and res_x and res_y:
            scale_x = 206265.0 * sensor_w_mm / (focal_length_mm * res_x)
            scale_y = 206265.0 * sensor_h_mm / (focal_length_mm * res_y)
            pixel_scale = (scale_x + scale_y) / 2.0

        if pixel_scale is None or pixel_scale <= 0:
            pixel_scale = settings.astrometry_pixel_scale_arcsec

        fov_diag_arcsec = None
        if sensor_w_mm and sensor_h_mm and focal_length_mm:
            import math

            fx = 2 * math.atan(sensor_w_mm / (2 * focal_length_mm))
            fy = 2 * math.atan(sensor_h_mm / (2 * focal_length_mm))
            fx_deg = math.degrees(fx)
            fy_deg = math.degrees(fy)
            fov_diag_arcsec = (fx_deg**2 + fy_deg**2) ** 0.5 * 3600.0
        elif pixel_scale and res_x and res_y:
            fov_diag_arcsec = ((pixel_scale * res_x) ** 2 + (pixel_scale * res_y) ** 2) ** 0.5

        if not fov_diag_arcsec:
            logger.warning("FOV inputs missing; using default centering tolerance (120 arcsec).")
            return {
                "center_arcsec": 120.0,
                "acquire_arcsec": 120.0,
                "center_px": 0.0,
                "acquire_px": 0.0,
            }

        fov_radius_arcsec = fov_diag_arcsec / 2.0
        acquire_arcsec = settings.workflow_acquire_fraction * fov_radius_arcsec
        center_arcsec = max(
            settings.workflow_center_fraction * fov_radius_arcsec,
            settings.workflow_center_floor_arcsec,
        )
        if rate_arcsec_per_min is not None:
            motion_arcsec = rate_arcsec_per_min * (settings.workflow_slew_settle_time_sec / 60.0)
            center_arcsec += motion_arcsec

        center_px = center_arcsec / pixel_scale if pixel_scale else 0.0
        acquire_px = acquire_arcsec / pixel_scale if pixel_scale else 0.0
        return {
            "center_arcsec": float(center_arcsec),
            "acquire_arcsec": float(acquire_arcsec),
            "center_px": float(center_px),
            "acquire_px": float(acquire_px),
        }

    @staticmethod
    def _compute_scale_bounds(
        pixel_scale: float | None,
        fallback_low: float | None,
        fallback_high: float | None,
    ) -> tuple[float | None, float | None]:
        if fallback_low is not None and fallback_high is not None:
            return fallback_low, fallback_high
        if pixel_scale and pixel_scale > 0:
            return pixel_scale * 0.5, pixel_scale * 2.0
        return fallback_low, fallback_high


__all__ = ["SequentialCaptureService"]
