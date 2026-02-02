"""Analysis service for source detection and association."""

from __future__ import annotations

import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from photutils.detection import DAOStarFinder
from sqlmodel import Session, select

from app.core.config import settings
from app.models import CaptureLog, Measurement, NeoEphemeris, CandidateAssociation
from app.services.star_subtraction import CatalogStarSubtractor

logger = logging.getLogger(__name__)


class AnalysisService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def detect_sources(self, path: Path, wcs: WCS | None = None) -> List[dict[str, Any]]:
        """Detect all sources in the image and return their centroids and properties."""
        try:
            data = fits.getdata(path)
        except Exception:
            return []

        if data is None:
            return []

        data = np.asarray(data, dtype=float)
        mean, median, std = sigma_clipped_stats(data, sigma=3.0)
        threshold = median + (5.0 * std)
        logger.debug(
            "Source detection stats: mean=%.3f median=%.3f std=%.3f threshold=%.3f",
            mean,
            median,
            std,
            threshold,
        )

        try:
            # FWHM=4.0 is a reasonable default for typical seeing
            finder = DAOStarFinder(fwhm=4.0, threshold=threshold - median)
            sources = finder(data - median)
        except Exception:
            return []

        if sources is None or len(sources) == 0:
            return []

        results = []
        for source in sources:
            x = float(source["xcentroid"])
            y = float(source["ycentroid"])
            flux = float(source["flux"])
            peak = float(source["peak"])
            snr = float(peak / std) if std else 0.0

            ra_deg = None
            dec_deg = None

            if wcs:
                # Convert pixel to sky coordinates
                sky = wcs.pixel_to_world(x, y)
                ra_deg = float(sky.ra.deg)
                dec_deg = float(sky.dec.deg)

            results.append({
                "x": x,
                "y": y,
                "flux": flux,
                "peak": peak,
                "snr": snr,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
            })

        return results

    def detect_sources_with_star_subtraction(
        self,
        path: Path,
        wcs: WCS,
        target_ra: float | None = None,
        target_dec: float | None = None,
        exclusion_radius_arcsec: float = 0.0
    ) -> Tuple[List[dict[str, Any]], int]:
        """
        Detect sources after subtracting field stars.

        Uses astrometry.net .corr file to subtract catalog stars,
        then detects remaining sources (like the asteroid).

        Args:
            path: Path to FITS file
            wcs: WCS solution
            target_ra: Optional target RA in degrees (for exclusion zone)
            target_dec: Optional target Dec in degrees (for exclusion zone)
            exclusion_radius_arcsec: Don't subtract within this radius of target (default 0 = no exclusion)

        Returns:
            Tuple of (detected sources, number of stars subtracted)

        Note:
            For proper blind detection (no ephemeris bias), call with
            target_ra=None, target_dec=None, exclusion_radius_arcsec=0.0
            This ensures all catalog stars are subtracted equally.
        """
        try:
            data = fits.getdata(path)
        except Exception as e:
            logger.error(f"Could not load FITS data from {path}: {e}")
            return [], 0

        if data is None:
            return [], 0

        data = np.asarray(data, dtype=float)

        # Subtract catalog stars (blind mode if no target specified)
        subtractor = CatalogStarSubtractor(path)
        cleaned_data, stars_subtracted = subtractor.subtract_stars(
            data, target_ra, target_dec, exclusion_radius_arcsec
        )
        if target_ra is None:
            logger.info(
                "Star subtraction complete (blind mode): removed=%d stars",
                stars_subtracted,
            )
        else:
            logger.info(
                "Star subtraction complete: removed=%d exclusion_radius=%.1f\"",
                stars_subtracted,
                exclusion_radius_arcsec,
            )

        # Detect sources in cleaned image with lower threshold
        mean, median, std = sigma_clipped_stats(cleaned_data, sigma=3.0)
        threshold = median + (3.0 * std)  # Lower threshold after star removal
        logger.debug(
            "Post-subtraction stats: mean=%.3f median=%.3f std=%.3f threshold=%.3f",
            mean,
            median,
            std,
            threshold,
        )

        try:
            finder = DAOStarFinder(fwhm=4.0, threshold=threshold - median)
            sources = finder(cleaned_data - median)
        except Exception as e:
            logger.warning(f"Source detection failed: {e}")
            return [], stars_subtracted

        if sources is None or len(sources) == 0:
            logger.debug("No sources detected after star subtraction")
            return [], stars_subtracted

        # Convert to results with WCS
        results = []
        for source in sources:
            x = float(source["xcentroid"])
            y = float(source["ycentroid"])
            flux = float(source["flux"])
            peak = float(source["peak"])
            snr = float(peak / std) if std else 0.0

            # Convert pixel to sky coordinates
            sky = wcs.pixel_to_world(x, y)
            ra_deg = float(sky.ra.deg)
            dec_deg = float(sky.dec.deg)

            results.append({
                "x": x,
                "y": y,
                "flux": flux,
                "peak": peak,
                "snr": snr,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
            })

        logger.info(f"Detected {len(results)} sources after subtracting {stars_subtracted} catalog stars")
        return results, stars_subtracted

    def find_best_match(
        self, 
        detections: List[dict[str, Any]], 
        predicted_ra: float, 
        predicted_dec: float, 
        tolerance_arcsec: float = 5.0
    ) -> Optional[dict[str, Any]]:
        """Find the detection closest to the predicted position within tolerance."""
        best_match = None
        min_dist = float("inf")
        
        for det in detections:
            ra = det.get("ra_deg")
            dec = det.get("dec_deg")
            if ra is None or dec is None:
                continue
                
            # Simple Euclidean distance for small separations (sufficient for matching)
            # Correct for cos(dec)
            cos_dec = math.cos(math.radians(predicted_dec))
            d_ra = (ra - predicted_ra) * cos_dec
            d_dec = dec - predicted_dec
            dist_deg = math.sqrt(d_ra**2 + d_dec**2)
            dist_arcsec = dist_deg * 3600.0
            
            if dist_arcsec < tolerance_arcsec and dist_arcsec < min_dist:
                min_dist = dist_arcsec
                best_match = det
                
        return best_match

    def auto_associate(
        self,
        db: Session | None = None,
        capture: CaptureLog | None = None,
        wcs: WCS | None = None,
        capture_id: int | None = None,
        tolerance_arcsec: float = 10.0,
        use_star_subtraction: bool = True,
    ) -> Optional[CandidateAssociation]:
        """
        Attempt to automatically associate a capture with its target ephemeris.

        Follows proper workflow separation to avoid circular logic:
        A. Detect sources blindly (no ephemeris influence)
        B. Astrometry already solved (WCS provided)
        C. Query ephemeris and compare predictions to measurements

        Args:
            db: Database session
            capture: Capture log entry
            wcs: WCS solution
            use_star_subtraction: If True, subtract catalog stars before detection

        Returns:
            CandidateAssociation if successful, None otherwise
        """
        db = db or self.session
        if db is None:
            raise RuntimeError("Database session required for association")

        if capture is None and capture_id is not None:
            capture = db.get(CaptureLog, capture_id)

        if capture is None:
            logger.error("Association failed: capture not found")
            return None

        if not capture.target or capture.target == "unknown":
            logger.debug("No target specified, cannot auto-associate")
            return None

        if wcs is None:
            path = Path(capture.path)
            wcs_path = path.with_suffix(".wcs")
            try:
                if wcs_path.exists():
                    wcs = WCS(str(wcs_path))
                    logger.info("Loaded WCS from %s", wcs_path)
                else:
                    wcs = WCS(fits.getheader(path))
                    logger.info("Loaded WCS from FITS header for %s", path)
            except Exception as exc:
                logger.warning("Failed to load WCS for %s: %s", capture.path, exc)
                return None

        logger.info(
            "Starting auto-association: capture_id=%s target=%s path=%s",
            capture.id,
            capture.target,
            capture.path,
        )

        # =====================================================================
        # STEP A: Detect sources BLINDLY (no ephemeris data used)
        # This is critical for scientific validity - detection must be
        # independent of predictions to avoid circular logic.
        # =====================================================================
        stars_subtracted = 0
        if use_star_subtraction:
            # Subtract ALL catalog stars (no exclusion zone)
            detections, stars_subtracted = self.detect_sources_with_star_subtraction(
                Path(capture.path),
                wcs,
                target_ra=None,              # No predicted position
                target_dec=None,             # No exclusion zone
                exclusion_radius_arcsec=0.0  # Subtract everything
            )
        else:
            detections = self.detect_sources(Path(capture.path), wcs)

        if not detections:
            logger.warning(f"No sources detected in {capture.path}")
            return None

        logger.info(
            "Detected %d sources blindly (no ephemeris bias, stars_subtracted=%d)",
            len(detections),
            stars_subtracted,
        )

        # =====================================================================
        # STEP B: Astrometry already solved
        # All detections now have independent (RA, Dec) measurements from WCS
        # =====================================================================

        # =====================================================================
        # STEP C: NOW query ephemeris (AFTER blind detection is complete)
        # Compare independent measurements to predictions
        # =====================================================================
        ephems = db.exec(
            select(NeoEphemeris)
            .where(NeoEphemeris.trksub == capture.target)
            .order_by(NeoEphemeris.epoch)
        ).all()

        if not ephems:
            logger.warning(f"No ephemeris found for target {capture.target}")
            return None

        logger.info("Loaded %d ephemeris points for %s", len(ephems), capture.target)

        best_eph = None
        min_diff = float("inf")
        before = None
        after = None
        for eph in ephems:
            diff = abs((eph.epoch - capture.started_at).total_seconds())
            if diff < min_diff:
                min_diff = diff
                best_eph = eph
            if eph.epoch <= capture.started_at:
                before = eph
            if eph.epoch >= capture.started_at and after is None:
                after = eph

        if not best_eph:
            logger.warning("No ephemeris available for %s", capture.target)
            return None

        use_ra = best_eph.ra_deg
        use_dec = best_eph.dec_deg
        use_epoch = best_eph.epoch

        if min_diff > 300:  # > 5 mins
            logger.warning(f"Nearest ephemeris is {min_diff:.0f}s away (> 300s limit)")
            interp = self._interpolate_ephemeris(before, after, capture.started_at)
            if interp is None:
                return None
            use_ra, use_dec, use_epoch = interp
            logger.info(
                "Using interpolated ephemeris at %s (bracketed by %s, %s)",
                capture.started_at,
                before.epoch if before else None,
                after.epoch if after else None,
            )
        else:
            logger.info(f"Comparing to ephemeris from {best_eph.epoch} (Δt={min_diff:.1f}s)")

        logger.info(
            "Ephemeris prediction: RA=%.6f Dec=%.6f",
            use_ra,
            use_dec,
        )

        # 3. Find Best Match
        match = self.find_best_match(
            detections,
            use_ra,
            use_dec,
            tolerance_arcsec=tolerance_arcsec
        )

        if not match:
            logger.warning(
                f"No match within {tolerance_arcsec}\" of predicted position "
                f"({use_ra:.5f}, {use_dec:.5f})"
            )
            return None

        logger.info(
            "Best match within %.1f\": RA=%.6f Dec=%.6f",
            tolerance_arcsec,
            match["ra_deg"],
            match["dec_deg"],
        )

        try:
            self._save_annotation_png(
                fits_path=Path(capture.path),
                wcs=wcs,
                capture_id=capture.id,
                predicted_ra=use_ra,
                predicted_dec=use_dec,
                matched_ra=match["ra_deg"],
                matched_dec=match["dec_deg"],
                radius_arcsec=tolerance_arcsec,
            )
        except Exception as exc:
            logger.warning("Failed to save annotation PNG for %s: %s", capture.path, exc)

        # 4. Calculate Residual
        residual_arcsec = self._calculate_residual(
            match["ra_deg"], match["dec_deg"],
            use_ra, use_dec
        )

        logger.info(
            f"Matched source at ({match['ra_deg']:.5f}, {match['dec_deg']:.5f}) "
            f"with residual {residual_arcsec:.2f}\" (SNR={match.get('snr', 0):.1f})"
        )

        # 5. Create Association with quality metrics
        assoc = CandidateAssociation(
            capture_id=capture.id,
            ra_deg=match["ra_deg"],
            dec_deg=match["dec_deg"],
            predicted_ra_deg=use_ra,
            predicted_dec_deg=use_dec,
            residual_arcsec=residual_arcsec,
            snr=match.get("snr"),
            peak_counts=match.get("peak"),
            method="auto",
            stars_subtracted=stars_subtracted if use_star_subtraction else None,
            created_at=datetime.utcnow(),
        )
        db.add(assoc)
        db.commit()
        db.refresh(assoc)

        logger.info(
            "Association saved: id=%s capture_id=%s residual=%.2f\" snr=%s stars_subtracted=%s",
            assoc.id,
            capture.id,
            residual_arcsec,
            match.get("snr"),
            stars_subtracted,
        )

        return assoc

    @staticmethod
    def _interpolate_ephemeris(
        before: NeoEphemeris | None,
        after: NeoEphemeris | None,
        when: datetime,
    ) -> tuple[float, float, datetime] | None:
        if before is None or after is None:
            return None
        if before.ra_deg is None or before.dec_deg is None:
            return None
        if after.ra_deg is None or after.dec_deg is None:
            return None
        if before.epoch == after.epoch:
            return (before.ra_deg, before.dec_deg, before.epoch)
        total = (after.epoch - before.epoch).total_seconds()
        if total <= 0:
            return None
        fraction = (when - before.epoch).total_seconds() / total
        fraction = max(0.0, min(1.0, fraction))
        ra = AnalysisService._interpolate_angle(before.ra_deg, after.ra_deg, fraction)
        dec = before.dec_deg + (after.dec_deg - before.dec_deg) * fraction
        return (ra, dec, when)

    @staticmethod
    def _interpolate_angle(start: float, end: float, fraction: float) -> float:
        delta = ((end - start + 180.0) % 360.0) - 180.0
        return (start + delta * fraction) % 360.0

    def record_measurement_from_association(
        self,
        db: Session,
        capture: CaptureLog,
        association: CandidateAssociation,
        exposure_seconds: float | None,
    ) -> Measurement:
        from datetime import timedelta

        if exposure_seconds:
            obs_time = capture.started_at + timedelta(seconds=exposure_seconds / 2.0)
        else:
            obs_time = capture.started_at

        ra_uncert = association.residual_arcsec
        if ra_uncert is None:
            ra_uncert = settings.astrometry_default_seeing_arcsec or 1.0
        dec_uncert = ra_uncert

        band = settings.default_band or "R"
        filter_name = getattr(capture, "filter_name", None)
        if filter_name:
            band = str(filter_name)

        measured_mag, mag_sigma, measured_band = self._measure_photometric_mag(
            capture=capture,
            association=association,
        )
        if measured_band:
            band = measured_band
        if measured_mag is None:
            logger.warning(
                "Photometric magnitude unavailable for capture %s; magnitude will be omitted",
                capture.id,
            )

        meas = Measurement(
            capture_id=capture.id,
            target=capture.target or "unknown",
            obs_time=obs_time,
            ra_deg=association.ra_deg,
            dec_deg=association.dec_deg,
            ra_uncert_arcsec=ra_uncert,
            dec_uncert_arcsec=dec_uncert,
            magnitude=measured_mag,
            mag_sigma=mag_sigma,
            band=band,
            exposure_seconds=exposure_seconds,
            tracking_mode=None,
            station_code=settings.station_code,
            observer=settings.observer_initials,
            software=settings.software_id,
            flags=None,
            reviewed=False,
            ast_cat="GaiaDR3",
        )
        db.add(meas)
        db.commit()
        db.refresh(meas)
        logger.info(
            "Measurement stored: id=%s capture_id=%s obs_time=%s ra=%.6f dec=%.6f",
            meas.id,
            capture.id,
            obs_time.isoformat(),
            association.ra_deg,
            association.dec_deg,
        )
        return meas

    def _measure_photometric_mag(
        self,
        *,
        capture: CaptureLog,
        association: CandidateAssociation,
    ) -> tuple[float | None, float | None, str | None]:
        path = Path(capture.path)
        if not path.exists():
            return None, None, None

        corr_path = path.with_suffix(".corr")
        if not corr_path.exists():
            logger.warning("No .corr file for photometry: %s", corr_path)
            return None, None, None

        try:
            data = fits.getdata(path)
        except Exception as exc:
            logger.warning("Failed to load FITS data for photometry: %s", exc)
            return None, None, None

        if data is None:
            return None, None, None

        data = np.asarray(data, dtype=float)

        try:
            wcs = WCS(str(path.with_suffix(".wcs")))
        except Exception:
            try:
                wcs = WCS(fits.getheader(path))
            except Exception as exc:
                logger.warning("Failed to load WCS for photometry: %s", exc)
                return None, None, None

        catalog_stars, band = self._load_catalog_photometry(corr_path)
        if not catalog_stars:
            logger.warning("No catalog stars with magnitudes found in %s", corr_path)
            return None, None, None

        aperture = settings.photometry_aperture_radius_px
        annulus_in = settings.photometry_annulus_r_in_px
        annulus_out = settings.photometry_annulus_r_out_px

        zp_values: list[float] = []
        for star in catalog_stars:
            flux = self._aperture_flux(
                data,
                star["x"],
                star["y"],
                aperture,
                annulus_in,
                annulus_out,
            )
            if flux is None or flux <= 0:
                continue
            mag = star.get("mag")
            if mag is None or not np.isfinite(mag):
                continue
            zp_values.append(float(mag) + 2.5 * math.log10(flux))

        if len(zp_values) < settings.photometry_min_cal_stars:
            logger.warning(
                "Insufficient calibration stars for photometry: %s/%s",
                len(zp_values),
                settings.photometry_min_cal_stars,
            )
            return None, None, None

        zp = float(np.median(zp_values))
        zp_std = float(np.std(zp_values)) if len(zp_values) > 1 else None

        try:
            target_x, target_y = wcs.world_to_pixel_values(
                association.ra_deg, association.dec_deg
            )
        except Exception as exc:
            logger.warning("Failed to convert association coords to pixels: %s", exc)
            return None, None, None

        target_flux = self._aperture_flux(
            data,
            float(target_x),
            float(target_y),
            aperture,
            annulus_in,
            annulus_out,
        )
        if target_flux is None or target_flux <= 0:
            logger.warning("Target flux non-positive; cannot compute magnitude")
            return None, None, None

        mag = zp - 2.5 * math.log10(target_flux)
        return mag, zp_std, band

    @staticmethod
    def _load_catalog_photometry(corr_path: Path) -> tuple[list[dict[str, Any]], str | None]:
        try:
            with fits.open(corr_path) as hdul:
                if len(hdul) < 2 or hdul[1].data is None:
                    return [], None
                data = hdul[1].data
                columns = [name.lower() for name in data.columns.names]
                mag_candidates = [
                    "index_mag",
                    "mag",
                    "index_mag_g",
                    "mag_g",
                    "gmag",
                    "index_mag_r",
                    "mag_r",
                    "rmag",
                    "magv",
                    "mag_v",
                ]
                mag_col = None
                for candidate in mag_candidates:
                    if candidate in columns:
                        mag_col = candidate
                        break
                if mag_col is None:
                    for name in columns:
                        if "mag" in name:
                            mag_col = name
                            break
                if mag_col is None:
                    return [], None

                band = None
                if "g" in mag_col:
                    band = "G"
                elif "r" in mag_col:
                    band = "R"
                elif "v" in mag_col:
                    band = "V"

                stars: list[dict[str, Any]] = []
                for row in data:
                    try:
                        stars.append(
                            {
                                "x": float(row["field_x"]),
                                "y": float(row["field_y"]),
                                "mag": float(row[mag_col]),
                            }
                        )
                    except Exception:
                        continue
                return stars, band
        except Exception as exc:
            logger.warning("Failed to read catalog photometry from %s: %s", corr_path, exc)
            return [], None

    @staticmethod
    def _aperture_flux(
        data: np.ndarray,
        x: float,
        y: float,
        radius: float,
        annulus_in: float,
        annulus_out: float,
    ) -> float | None:
        h, w = data.shape
        if not (0 <= x < w and 0 <= y < h):
            return None

        y_indices, x_indices = np.ogrid[:h, :w]
        distances = np.sqrt((x_indices - x) ** 2 + (y_indices - y) ** 2)

        aperture_mask = distances <= radius
        annulus_mask = (distances >= annulus_in) & (distances <= annulus_out)

        if not np.any(aperture_mask):
            return None

        aperture_sum = float(np.sum(data[aperture_mask]))
        background = 0.0
        if np.any(annulus_mask):
            background = float(np.median(data[annulus_mask]))
        aperture_area = float(np.sum(aperture_mask))
        return aperture_sum - background * aperture_area

    def _predict_mag_from_ephemeris(
        self,
        db: Session,
        capture: CaptureLog,
    ) -> float | None:
        if not capture.target:
            return None

        ephems = db.exec(
            select(NeoEphemeris)
            .where(NeoEphemeris.trksub == capture.target)
            .order_by(NeoEphemeris.epoch)
        ).all()
        if not ephems:
            return None

        best_eph = None
        min_diff = float("inf")
        before = None
        after = None
        for eph in ephems:
            diff = abs((eph.epoch - capture.started_at).total_seconds())
            if diff < min_diff:
                min_diff = diff
                best_eph = eph
            if eph.epoch <= capture.started_at:
                before = eph
            if eph.epoch >= capture.started_at and after is None:
                after = eph

        def _mag_value(eph: NeoEphemeris | None) -> float | None:
            if eph is None:
                return None
            if eph.v_mag_predicted is not None:
                return eph.v_mag_predicted
            return eph.magnitude

        if best_eph is None:
            return None

        mag = _mag_value(best_eph)

        if min_diff > 300 and before and after:
            mag_before = _mag_value(before)
            mag_after = _mag_value(after)
            if mag_before is not None and mag_after is not None:
                total = (after.epoch - before.epoch).total_seconds()
                if total > 0:
                    fraction = (capture.started_at - before.epoch).total_seconds() / total
                    fraction = max(0.0, min(1.0, fraction))
                    mag = mag_before + (mag_after - mag_before) * fraction

        if mag is None:
            logger.warning("No predicted magnitude available for %s", capture.target)
        return mag

    def _calculate_residual(
        self,
        ra1: float,
        dec1: float,
        ra2: float,
        dec2: float
    ) -> float:
        """Calculate angular separation in arcseconds."""
        # Simple Euclidean for small separations with cos(dec) correction
        cos_dec = math.cos(math.radians((dec1 + dec2) / 2))
        d_ra = (ra1 - ra2) * cos_dec
        d_dec = dec1 - dec2
        dist_deg = math.sqrt(d_ra**2 + d_dec**2)
        return dist_deg * 3600.0

    @staticmethod
    def _save_annotation_png(
        *,
        fits_path: Path,
        wcs: WCS,
        capture_id: int | None,
        predicted_ra: float,
        predicted_dec: float,
        matched_ra: float,
        matched_dec: float,
        radius_arcsec: float,
    ) -> None:
        data = fits.getdata(fits_path)
        if data is None or data.size == 0:
            raise ValueError("FITS image data is empty")
        if data.ndim > 2:
            data = data[0]

        scales = proj_plane_pixel_scales(wcs)
        scale_arcsec = float(np.mean(scales)) * 3600.0
        radius_px = max(5.0, radius_arcsec / scale_arcsec) * 5.0  # 5x larger diameter

        pred_x, pred_y = wcs.wcs_world2pix(predicted_ra, predicted_dec, 0)
        match_x, match_y = wcs.wcs_world2pix(matched_ra, matched_dec, 0)

        vmin, vmax = np.nanpercentile(data, [1.0, 99.0])
        if not np.isfinite(vmin) or not np.isfinite(vmax):
            vmin, vmax = float(np.nanmin(data)), float(np.nanmax(data))

        fig, ax = plt.subplots(figsize=(8, 5))
        ax.imshow(data, cmap="gray", origin="lower", vmin=vmin, vmax=vmax)
        ax.add_patch(plt.Circle((pred_x, pred_y), radius_px, color="yellow", fill=False, linewidth=0.5))
        ax.add_patch(plt.Circle((match_x, match_y), radius_px, color="cyan", fill=False, linewidth=0.5))
        legend_handles = [
            plt.Line2D([0], [0], color="yellow", marker="o", markerfacecolor="none", linestyle=""),
            plt.Line2D([0], [0], color="cyan", marker="o", markerfacecolor="none", linestyle=""),
        ]
        ax.legend(legend_handles, ["Predicted", "Associated"], loc="upper right", frameon=True)
        ax.set_axis_off()

        suffix = f"_annotated_{capture_id}" if capture_id is not None else "_annotated"
        output_path = fits_path.with_suffix("").with_name(f"{fits_path.stem}{suffix}.png")
        fig.savefig(output_path, dpi=200, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)
        logger.info("Saved annotated image: %s", output_path)

    def resolve_click(self, capture: CaptureLog, click_x: float | None = None, click_y: float | None = None, polygon: list[dict[str, float]] | None = None, crop_size: int = 20) -> dict[str, Any] | None:
        """Resolve a click or polygon on an image to a precise centroid and RA/Dec."""
        import logging
        path = Path(capture.path)
        if not path.exists():
            logging.error(f"File not found: {path}")
            return None
            
        try:
            # Load data
            with fits.open(path) as hdul:
                data = hdul[0].data
                if data is None:
                    return None
                data = np.asarray(data, dtype=float)
                
            h, w = data.shape
            
            if polygon:
                # Polygon mode
                from PIL import Image, ImageDraw
                
                # Convert list of dicts to list of tuples
                vertices = [(p["x"], p["y"]) for p in polygon]
                logging.info(f"Polygon vertices: {vertices}")
                
                # Create mask using PIL
                # Note: PIL uses (x, y) which matches our vertices
                mask_img = Image.new('L', (w, h), 0)
                ImageDraw.Draw(mask_img).polygon(vertices, outline=1, fill=1)
                mask = np.array(mask_img)
                
                # Apply mask to data
                weighted_data = data * mask
                
                if np.all(weighted_data == 0):
                    logging.warning("Polygon mask resulted in all zeros")
                    return None
                    
                # Find max index in the whole image
                y_max, x_max = np.unravel_index(np.argmax(weighted_data), weighted_data.shape)
                
                global_x = float(x_max)
                global_y = float(y_max)
                
                logging.info(f"Polygon max at global: {global_x}, {global_y}")
                
                # Refine with centroiding around this peak
                x_int, y_int = int(global_x), int(global_y)
                
                # Reuse the crop logic below
                x_start = max(0, x_int - crop_size)
                x_end = min(w, x_int + crop_size)
                y_start = max(0, y_int - crop_size)
                y_end = min(h, y_int + crop_size)
                
                crop = data[y_start:y_end, x_start:x_end]
                
            elif click_x is not None and click_y is not None:
                # Click mode
                x_int, y_int = int(round(click_x)), int(round(click_y))
                
                x_start = max(0, x_int - crop_size)
                x_end = min(w, x_int + crop_size)
                y_start = max(0, y_int - crop_size)
                y_end = min(h, y_int + crop_size)
                
                crop = data[y_start:y_end, x_start:x_end]
            else:
                return None
                
            if crop.size == 0:
                logging.error("Crop is empty")
                return None
                
            # Find centroid in crop
            mean, median, std = sigma_clipped_stats(crop, sigma=3.0)
            threshold = median + (3.0 * std) # Lower threshold for manual clicks
            
            finder = DAOStarFinder(fwhm=4.0, threshold=threshold - median)
            sources = finder(crop - median)
            
            if sources is None or len(sources) == 0:
                # Fallback: just use the brightest pixel in crop
                y_max, x_max = np.unravel_index(np.argmax(crop), crop.shape)
                local_x, local_y = float(x_max), float(y_max)
                peak = float(crop[y_max, x_max])
                snr = (peak - median) / std if std else 0.0
            else:
                # Find source closest to center of crop
                cx, cy = crop.shape[1] / 2, crop.shape[0] / 2
                best_dist = float("inf")
                best_source = None
                
                for source in sources:
                    sx, sy = source["xcentroid"], source["ycentroid"]
                    dist = (sx - cx)**2 + (sy - cy)**2
                    if dist < best_dist:
                        best_dist = dist
                        best_source = source
                        
                if best_source:
                    local_x = float(best_source["xcentroid"])
                    local_y = float(best_source["ycentroid"])
                    peak = float(best_source["peak"])
                    snr = float(peak / std) if std else 0.0
                else:
                    return None

            # Convert back to global image coordinates
            global_x = x_start + local_x
            global_y = y_start + local_y
            
            # Get WCS
            wcs_path = path.with_suffix(".wcs")
            if wcs_path.exists():
                import warnings
                from astropy.wcs import FITSFixedWarning
                
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", FITSFixedWarning)
                    wcs = WCS(str(wcs_path))
                    
                sky = wcs.pixel_to_world(global_x, global_y)
                ra_deg = float(sky.ra.deg)
                dec_deg = float(sky.dec.deg)
            else:
                logging.error("WCS file not found")
                return None
                
            return {
                "x": global_x,
                "y": global_y,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                "snr": snr,
                "peak": peak
            }
            
        except Exception as exc:
            logging.exception(f"Error resolving click: {exc}")
            return None
