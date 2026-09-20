"""Analysis service for source detection and association."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
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

    def get_wcs_quality(self, fits_path: Path) -> dict[str, Any]:
        """
        Extract WCS quality metrics from astrometry.net output files.

        Returns dict with:
            - wcs_rms_arcsec: RMS of WCS fit (arcsec)
            - n_match: Number of matched reference stars
            - n_index: Number of index stars in field
            - quality_grade: 'A' (good), 'B' (acceptable), 'C' (marginal), 'D' (poor)
        """
        result = {
            'wcs_rms_arcsec': None,
            'n_match': None,
            'n_index': None,
            'quality_grade': None,
        }

        # Try to get stats from .corr file (correlation file)
        corr_path = fits_path.with_suffix('.corr')
        if corr_path.exists():
            try:
                with fits.open(corr_path) as hdul:
                    if len(hdul) >= 2 and hdul[1].data is not None:
                        data = hdul[1].data
                        result['n_match'] = len(data)

                        # Compute RMS from field vs index positions if available
                        if 'field_x' in data.dtype.names and 'index_x' in data.dtype.names:
                            dx = data['field_x'] - data['index_x']
                            dy = data['field_y'] - data['index_y']
                            # This is pixel RMS - need to convert to arcsec
                            pixel_rms = float(np.sqrt(np.mean(dx**2 + dy**2)))
                            # Get pixel scale from WCS
                            wcs_path = fits_path.with_suffix('.wcs')
                            if wcs_path.exists():
                                try:
                                    wcs = WCS(str(wcs_path))
                                    scales = proj_plane_pixel_scales(wcs)
                                    scale_arcsec = float(np.mean(scales)) * 3600.0
                                    result['wcs_rms_arcsec'] = pixel_rms * scale_arcsec
                                except Exception:
                                    pass
            except Exception as e:
                logger.debug("Could not read .corr file for WCS quality: %s", e)

        # Try to get stats from .match file if available
        match_path = fits_path.with_suffix('.match')
        if match_path.exists() and result['wcs_rms_arcsec'] is None:
            try:
                with fits.open(match_path) as hdul:
                    header = hdul[0].header
                    # astrometry.net stores various stats in header
                    if 'QRUNPIX' in header:
                        # Quad uncertainty in pixels
                        pixel_scale = settings.astrometry_pixel_scale_arcsec
                        result['wcs_rms_arcsec'] = float(header['QRUNPIX']) * pixel_scale
            except Exception as e:
                logger.debug("Could not read .match file for WCS quality: %s", e)

        # Assign quality grade based on RMS
        rms = result['wcs_rms_arcsec']
        if rms is not None:
            if rms <= 0.5:
                result['quality_grade'] = 'A'  # Good
            elif rms <= 1.0:
                result['quality_grade'] = 'B'  # Acceptable
            elif rms <= 2.0:
                result['quality_grade'] = 'C'  # Marginal
            else:
                result['quality_grade'] = 'D'  # Poor

        return result

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
        # Optionally query Gaia for additional stars beyond .corr file
        subtractor = CatalogStarSubtractor(path)
        cleaned_data, stars_subtracted = subtractor.subtract_stars(
            data,
            target_ra,
            target_dec,
            exclusion_radius_arcsec,
            use_gaia=settings.star_subtraction_use_gaia,
            gaia_mag_limit=settings.star_subtraction_gaia_mag_limit,
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

        # Get pixel scale for centroid uncertainty conversion
        try:
            pixel_scales = proj_plane_pixel_scales(wcs)  # deg/pixel
            scale_arcsec = float(np.mean(pixel_scales)) * 3600.0
        except Exception:
            scale_arcsec = settings.astrometry_pixel_scale_arcsec

        # Get catalog star positions for "near bright star" flagging
        subtractor = CatalogStarSubtractor(path)
        catalog_stars = subtractor.get_catalog_stars(
            use_gaia=settings.star_subtraction_use_gaia,
            mag_limit=settings.star_subtraction_gaia_mag_limit,
        )

        # FWHM used for detection (from DAOStarFinder)
        fwhm_px = 4.0

        # Convert to results with WCS and morphology features
        results = []
        for source in sources:
            x = float(source["xcentroid"])
            y = float(source["ycentroid"])
            flux = float(source["flux"])
            peak = float(source["peak"])

            # SNR calculation: peak / background_std
            snr = float(peak / std) if std > 0 else 0.0

            # Morphology features from DAOStarFinder
            sharpness = float(source["sharpness"]) if "sharpness" in source.colnames else None
            roundness1 = float(source["roundness1"]) if "roundness1" in source.colnames else None
            roundness2 = float(source["roundness2"]) if "roundness2" in source.colnames else None

            # Average roundness for simpler gating
            roundness = None
            if roundness1 is not None and roundness2 is not None:
                roundness = (abs(roundness1) + abs(roundness2)) / 2.0

            # Build flags list
            flags = []

            # Morphology gating flags
            if sharpness is not None:
                if sharpness < 0.2:
                    flags.append('too_sharp')  # Likely cosmic ray
                elif sharpness > 1.0:
                    flags.append('too_extended')  # Likely galaxy or artifact

            if roundness is not None and roundness > 0.5:
                flags.append('elongated')  # Trailed or extended

            if snr < 5.0:
                flags.append('low_snr')

            # Check proximity to bright catalog stars
            near_bright_star = False
            for cat_star in catalog_stars:
                dist_px = math.sqrt((x - cat_star['x'])**2 + (y - cat_star['y'])**2)
                if dist_px < 20:  # Within 20 pixels of a catalog star
                    near_bright_star = True
                    break
            if near_bright_star:
                flags.append('near_bright_star')

            # Convert pixel to sky coordinates
            sky = wcs.pixel_to_world(x, y)
            ra_deg = float(sky.ra.deg)
            dec_deg = float(sky.dec.deg)

            # =====================================================================
            # Centroid uncertainty estimation (Phase 4)
            # σ_pix ≈ FWHM / (2.355 * SNR) for photon-noise limited centroiding
            # =====================================================================
            if snr > 0:
                sigma_pix = fwhm_px / (2.355 * snr)
            else:
                sigma_pix = fwhm_px  # Fallback: assume 1-FWHM uncertainty

            # Convert to sky uncertainty (arcsec)
            sigma_ra_arcsec = sigma_pix * scale_arcsec
            sigma_dec_arcsec = sigma_pix * scale_arcsec

            results.append({
                "x": x,
                "y": y,
                "flux": flux,
                "peak": peak,
                "snr": snr,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                # Morphology features (Phase 3)
                "fwhm_px": fwhm_px,
                "sharpness": sharpness,
                "roundness": roundness,
                "roundness1": roundness1,
                "roundness2": roundness2,
                "flags": flags,
                # Centroid uncertainty (Phase 4)
                "sigma_pix": sigma_pix,
                "sigma_ra_arcsec": sigma_ra_arcsec,
                "sigma_dec_arcsec": sigma_dec_arcsec,
            })

        # Log morphology summary
        flagged_count = sum(1 for r in results if r['flags'])
        logger.info(
            "Detected %d sources after subtracting %d catalog stars (%d flagged)",
            len(results),
            stars_subtracted,
            flagged_count,
        )
        return results, stars_subtracted

    def find_best_match(
        self,
        detections: List[dict[str, Any]],
        predicted_ra: float,
        predicted_dec: float,
        tolerance_arcsec: float = 5.0
    ) -> Optional[dict[str, Any]]:
        """Find the detection closest to the predicted position within tolerance.

        DEPRECATED: Use find_best_match_scored() for rigorous association.
        Kept for backward compatibility.
        """
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

    def find_best_match_scored(
        self,
        detections: List[dict[str, Any]],
        predicted_ra: float,
        predicted_dec: float,
        wcs_rms_arcsec: float | None = None,
        angular_rate_arcsec_sec: float | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """
        Find the best match using scoring instead of 'closest wins'.

        Implements data-driven match radius and likelihood scoring:
        1. Compute per-detection match radius from uncertainties
        2. Score each candidate by z-score + morphology penalties
        3. Select minimum score with quality checks

        Args:
            detections: List of detected sources with uncertainty info
            predicted_ra, predicted_dec: Ephemeris prediction
            wcs_rms_arcsec: WCS solution RMS (arcsec)
            angular_rate_arcsec_sec: Target motion rate for timing uncertainty

        Returns:
            Tuple of (best_match, scoring_details)
            scoring_details includes per-candidate scores for audit
        """
        k = settings.association_sigma_multiplier
        r_min = settings.association_min_radius_arcsec
        timing_uncert = settings.association_timing_uncertainty_sec
        z_max = settings.association_z_score_max

        # Default WCS uncertainty if not provided
        sigma_wcs = wcs_rms_arcsec if wcs_rms_arcsec else 1.0

        # Timing-induced positional uncertainty
        if angular_rate_arcsec_sec is not None and angular_rate_arcsec_sec > 0:
            sigma_timing = angular_rate_arcsec_sec * timing_uncert
        else:
            sigma_timing = 0.5  # Default small timing uncertainty

        candidates = []
        cos_dec = math.cos(math.radians(predicted_dec))

        for det in detections:
            ra = det.get("ra_deg")
            dec = det.get("dec_deg")
            if ra is None or dec is None:
                continue

            # Residual in arcsec
            d_ra_arcsec = (ra - predicted_ra) * cos_dec * 3600.0
            d_dec_arcsec = (dec - predicted_dec) * 3600.0
            dist_arcsec = math.sqrt(d_ra_arcsec**2 + d_dec_arcsec**2)

            # Centroid uncertainty from detection
            sigma_cent = det.get('sigma_ra_arcsec', 1.0)

            # Combined uncertainty (quadrature sum)
            sigma_total = math.sqrt(sigma_wcs**2 + sigma_cent**2 + sigma_timing**2)

            # Data-driven match radius
            r_match = max(r_min, k * sigma_total)

            # Skip if outside match radius
            if dist_arcsec > r_match:
                continue

            # Z-score (normalized residual)
            z_score = dist_arcsec / sigma_total if sigma_total > 0 else dist_arcsec

            # Morphology penalty
            morph_penalty = 0.0
            flags = det.get('flags', [])
            if 'near_bright_star' in flags:
                morph_penalty += settings.association_near_star_penalty
            if 'low_snr' in flags:
                morph_penalty += settings.association_low_snr_penalty
            # Additional penalty for morphology issues
            if 'too_sharp' in flags or 'too_extended' in flags or 'elongated' in flags:
                morph_penalty += 0.5

            total_score = z_score + morph_penalty

            candidates.append({
                'detection': det,
                'dist_arcsec': dist_arcsec,
                'd_ra_arcsec': d_ra_arcsec,
                'd_dec_arcsec': d_dec_arcsec,
                'sigma_total': sigma_total,
                'r_match': r_match,
                'z_score': z_score,
                'morph_penalty': morph_penalty,
                'total_score': total_score,
                'flags': flags,
            })

        scoring_details = {
            'n_candidates': len(candidates),
            'sigma_wcs': sigma_wcs,
            'sigma_timing': sigma_timing,
            'candidates': candidates,
        }

        if not candidates:
            return None, scoring_details

        # Sort by total score (lower is better)
        candidates.sort(key=lambda c: c['total_score'])

        best = candidates[0]

        # Quality gate: reject if z-score too high
        if best['z_score'] > z_max:
            logger.warning(
                "Best candidate rejected: z_score=%.2f > max=%.1f",
                best['z_score'],
                z_max,
            )
            return None, scoring_details

        # Assign quality grade based on z-score
        if best['z_score'] <= 1.5:
            quality_grade = 'A'
        elif best['z_score'] <= 2.5:
            quality_grade = 'B'
        else:
            quality_grade = 'C'

        # Add scoring info to the detection
        result = best['detection'].copy()
        result['_scoring'] = {
            'z_score': best['z_score'],
            'morph_penalty': best['morph_penalty'],
            'total_score': best['total_score'],
            'r_match_used': best['r_match'],
            'sigma_total': best['sigma_total'],
            'quality_grade': quality_grade,
            'residual_ra_arcsec': best['d_ra_arcsec'],
            'residual_dec_arcsec': best['d_dec_arcsec'],
        }

        # Log scoring details
        logger.info(
            "Association scoring: %d candidates, best z=%.2f morph=%.1f total=%.2f grade=%s",
            len(candidates),
            best['z_score'],
            best['morph_penalty'],
            best['total_score'],
            quality_grade,
        )
        if len(candidates) > 1:
            for i, c in enumerate(candidates[:3]):
                marker = " ← SELECTED" if i == 0 else ""
                logger.debug(
                    "  Candidate %d: z=%.2f morph=%.1f total=%.2f dist=%.2f\"%s",
                    i + 1,
                    c['z_score'],
                    c['morph_penalty'],
                    c['total_score'],
                    c['dist_arcsec'],
                    marker,
                )

        return result, scoring_details

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

        # =====================================================================
        # Extract WCS quality metrics for data-driven match radius
        # =====================================================================
        wcs_quality = self.get_wcs_quality(Path(capture.path))
        wcs_rms = wcs_quality.get('wcs_rms_arcsec')
        wcs_grade = wcs_quality.get('quality_grade')
        n_match = wcs_quality.get('n_match')

        if wcs_rms is not None:
            logger.info(
                "WCS quality: RMS=%.3f\" grade=%s n_match=%s",
                wcs_rms,
                wcs_grade or '?',
                n_match or '?',
            )
            if wcs_grade == 'D':
                logger.warning(
                    "WCS quality is POOR (RMS=%.2f\") - association may be unreliable",
                    wcs_rms,
                )
        else:
            logger.debug("WCS quality metrics not available")

        logger.info(
            "Starting auto-association: capture_id=%s target=%s path=%s",
            capture.id,
            capture.target,
            capture.path,
        )

        # =====================================================================
        # STEP 0: Compute mid-exposure time for ephemeris queries
        # NINA DATE-OBS is exposure start time, so we need:
        # t_mid = t_start + 0.5 * exposure_duration
        # =====================================================================
        exposure_seconds = None
        try:
            with fits.open(capture.path) as hdul:
                header = hdul[0].header
                exposure_seconds = header.get('EXPTIME') or header.get('EXPOSURE')
        except Exception as e:
            logger.warning("Could not read EXPTIME from FITS header: %s", e)

        if exposure_seconds is not None:
            t_mid = capture.started_at + timedelta(seconds=float(exposure_seconds) / 2.0)
            logger.info(
                "Mid-exposure timing: t_start=%s + %.1fs/2 = t_mid=%s",
                capture.started_at.isoformat(),
                exposure_seconds,
                t_mid.isoformat(),
            )
        else:
            t_mid = capture.started_at
            logger.warning(
                "EXPTIME not available, using capture start time: %s",
                capture.started_at.isoformat(),
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
        # Query by trksub OR candidate_id to handle both NEOCP designations
        # and numbered asteroids (where candidate_id = "433" but trksub might differ)
        from sqlalchemy import or_
        ephems = db.exec(
            select(NeoEphemeris)
            .where(
                or_(
                    NeoEphemeris.trksub == capture.target,
                    NeoEphemeris.candidate_id == capture.target,
                )
            )
            .order_by(NeoEphemeris.epoch)
        ).all()

        if not ephems:
            logger.warning(
                "No ephemeris found for target %s (checked trksub and candidate_id)",
                capture.target,
            )
            return None

        logger.info("Loaded %d ephemeris points for %s", len(ephems), capture.target)

        # Find ephemeris entries closest to mid-exposure time (t_mid)
        best_eph = None
        min_diff = float("inf")
        before = None
        after = None
        for eph in ephems:
            diff = abs((eph.epoch - t_mid).total_seconds())
            if diff < min_diff:
                min_diff = diff
                best_eph = eph
            if eph.epoch <= t_mid:
                before = eph
            if eph.epoch >= t_mid and after is None:
                after = eph

        if not best_eph:
            logger.warning("No ephemeris available for %s", capture.target)
            return None

        use_ra = best_eph.ra_deg
        use_dec = best_eph.dec_deg
        use_epoch = best_eph.epoch

        if min_diff > 300:  # > 5 mins
            logger.warning(f"Nearest ephemeris is {min_diff:.0f}s away from t_mid (> 300s limit)")
            interp = self._interpolate_ephemeris(before, after, t_mid)
            if interp is None:
                return None
            use_ra, use_dec, use_epoch = interp
            logger.info(
                "Using interpolated ephemeris at t_mid=%s (bracketed by %s, %s)",
                t_mid,
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

        # Compute angular rate for timing uncertainty (if we have bracketing ephems)
        angular_rate = None
        if before and after and before.epoch != after.epoch:
            dt = (after.epoch - before.epoch).total_seconds()
            if dt > 0:
                cos_dec_rate = math.cos(math.radians((before.dec_deg + after.dec_deg) / 2))
                d_ra = (after.ra_deg - before.ra_deg) * cos_dec_rate
                d_dec = after.dec_deg - before.dec_deg
                rate_deg_sec = math.sqrt(d_ra**2 + d_dec**2) / dt
                angular_rate = rate_deg_sec * 3600.0  # arcsec/sec
                logger.debug("Angular rate: %.4f arcsec/sec", angular_rate)

        # =====================================================================
        # STEP D: Find best match using scoring (not just "closest wins")
        # Uses data-driven match radius and likelihood scoring
        # =====================================================================
        match, scoring_details = self.find_best_match_scored(
            detections,
            use_ra,
            use_dec,
            wcs_rms_arcsec=wcs_rms,
            angular_rate_arcsec_sec=angular_rate,
        )

        if not match:
            # Fall back to simple distance-based matching with configured tolerance
            logger.warning(
                "Scoring-based match failed (%d candidates), trying distance-based fallback",
                scoring_details.get('n_candidates', 0),
            )
            match = self.find_best_match(
                detections,
                use_ra,
                use_dec,
                tolerance_arcsec=tolerance_arcsec
            )

        if not match:
            logger.warning(
                f"No match found near predicted position ({use_ra:.5f}, {use_dec:.5f})"
            )
            return None

        # Extract scoring info if available
        scoring_info = match.pop('_scoring', None)

        logger.info(
            "Associated detection: RA=%.6f Dec=%.6f SNR=%.1f",
            match["ra_deg"],
            match["dec_deg"],
            match.get("snr", 0),
        )
        if scoring_info:
            logger.info(
                "  Scoring: z=%.2f r_match=%.2f\" grade=%s",
                scoring_info.get('z_score', 0),
                scoring_info.get('r_match_used', 0),
                scoring_info.get('quality_grade', '?'),
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
            quality_grade=scoring_info.get("quality_grade") if scoring_info else None,
            z_score=scoring_info.get("z_score") if scoring_info else None,
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
                    # No magnitude column found - try Gaia fallback
                    logger.info("No magnitude column in .corr file, attempting Gaia photometry fallback")
                    return AnalysisService._query_gaia_photometry(corr_path)

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
    def _query_gaia_photometry(corr_path: Path) -> tuple[list[dict[str, Any]], str | None]:
        """
        Query Gaia DR3 for photometry when .corr file lacks magnitude data.

        Returns catalog stars with x, y, mag (Gaia G-band) by:
        1. Reading catalog star RA/Dec from .corr file
        2. Querying Gaia DR3 for stars in the field
        3. Matching Gaia stars to catalog stars by position
        4. Converting Gaia star positions to pixel coordinates
        """
        try:
            from astroquery.gaia import Gaia
        except ImportError:
            logger.warning("astroquery not available, cannot query Gaia for photometry")
            return [], None

        try:
            # Read WCS from corresponding .wcs file
            wcs_path = corr_path.with_suffix('.wcs')
            if not wcs_path.exists():
                logger.warning("WCS file not found: %s", wcs_path)
                return [], None

            wcs = WCS(fits.getheader(wcs_path))

            # Read catalog star positions from .corr file
            with fits.open(corr_path) as hdul:
                if len(hdul) < 2 or hdul[1].data is None:
                    return [], None
                data = hdul[1].data

                # Get field center from catalog stars
                catalog_ras = data['index_ra']
                catalog_decs = data['index_dec']
                center_ra = float(np.median(catalog_ras))
                center_dec = float(np.median(catalog_decs))

                # Calculate field radius (diagonal)
                ra_span = float(np.max(catalog_ras) - np.min(catalog_ras))
                dec_span = float(np.max(catalog_decs) - np.min(catalog_decs))
                field_radius_deg = 0.5 * np.sqrt(ra_span**2 + dec_span**2) * 1.2  # 20% margin

                logger.info(
                    "Querying Gaia DR3 for photometry: center=(%.3f, %.3f) radius=%.3f°",
                    center_ra, center_dec, field_radius_deg
                )

                # Query Gaia DR3
                query = f"""
                SELECT ra, dec, phot_g_mean_mag
                FROM gaiadr3.gaia_source
                WHERE 1=CONTAINS(
                    POINT('ICRS', ra, dec),
                    CIRCLE('ICRS', {center_ra}, {center_dec}, {field_radius_deg})
                )
                AND phot_g_mean_mag IS NOT NULL
                AND phot_g_mean_mag < 20
                ORDER BY phot_g_mean_mag ASC
                """

                # See star_subtraction.py's _query_gaia_stars for why this
                # needs a hard timeout with a non-blocking shutdown (a bad
                # connection at a field site can hang launch_job_async/
                # get_results indefinitely with zero error output).
                import concurrent.futures

                GAIA_PHOTOMETRY_TIMEOUT_SECONDS = 30

                def _run_query():
                    job = Gaia.launch_job_async(query, verbose=False)
                    return job.get_results()

                pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                future = pool.submit(_run_query)
                try:
                    result = future.result(timeout=GAIA_PHOTOMETRY_TIMEOUT_SECONDS)
                except concurrent.futures.TimeoutError:
                    logger.warning(
                        f"Gaia photometry query timed out after "
                        f"{GAIA_PHOTOMETRY_TIMEOUT_SECONDS}s -- continuing without it"
                    )
                    pool.shutdown(wait=False)
                    return [], None
                pool.shutdown(wait=False)

                if len(result) == 0:
                    logger.warning("No Gaia stars found in field")
                    return [], None

                logger.info("Found %d Gaia stars with photometry", len(result))

                # Match catalog stars to Gaia stars by position
                # Build catalog star list with RA/Dec
                catalog_stars = []
                for row in data:
                    catalog_stars.append({
                        'ra': float(row['index_ra']),
                        'dec': float(row['index_dec']),
                        'x': float(row['field_x']),
                        'y': float(row['field_y']),
                    })

                # Match Gaia stars to catalog stars (within 3 arcsec)
                matched_stars = []
                match_radius_deg = 3.0 / 3600.0  # 3 arcsec tolerance

                for gaia_star in result:
                    gaia_ra = float(gaia_star['ra'])
                    gaia_dec = float(gaia_star['dec'])
                    gaia_mag = float(gaia_star['phot_g_mean_mag'])

                    # Find closest catalog star
                    best_sep = float('inf')
                    best_match = None

                    for cat_star in catalog_stars:
                        # Angular separation (small angle approximation)
                        dra = (gaia_ra - cat_star['ra']) * np.cos(np.radians(gaia_dec))
                        ddec = gaia_dec - cat_star['dec']
                        sep = np.sqrt(dra**2 + ddec**2)

                        if sep < best_sep and sep < match_radius_deg:
                            best_sep = sep
                            best_match = cat_star

                    if best_match:
                        matched_stars.append({
                            'x': best_match['x'],
                            'y': best_match['y'],
                            'mag': gaia_mag,
                        })

                if len(matched_stars) == 0:
                    logger.warning("No Gaia stars matched to catalog stars")
                    return [], None

                logger.info("Matched %d Gaia stars to catalog stars", len(matched_stars))
                return matched_stars, "G"  # Gaia G-band

        except Exception as exc:
            logger.warning("Failed to query Gaia photometry: %s", exc)
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

        from sqlalchemy import or_
        ephems = db.exec(
            select(NeoEphemeris)
            .where(
                or_(
                    NeoEphemeris.trksub == capture.target,
                    NeoEphemeris.candidate_id == capture.target,
                )
            )
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
