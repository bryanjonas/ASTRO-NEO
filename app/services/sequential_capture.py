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
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import CaptureLog, CandidateAssociation
from app.services.file_poller import poll_for_fits_file, wait_for_file_size_stable
from app.services.horizons_client import HorizonsClient
from app.services.nina_client import NinaBridgeService
from app.services.solver import SolverService
from app.services.analysis import AnalysisService

logger = logging.getLogger(__name__)


class SequentialCaptureService:
    """
    Orchestrates sequential capture with confirmation loop.
    """

    def __init__(
        self,
        db: Session,
        nina_client: Optional[NinaBridgeService] = None,
        horizons_client: Optional[HorizonsClient] = None,
        solver: Optional[SolverService] = None,
        analysis: Optional[AnalysisService] = None,
    ):
        self.db = db
        self.nina = nina_client or NinaBridgeService()
        self.horizons = horizons_client or HorizonsClient()
        self.solver = solver or SolverService(db)
        self.analysis = analysis or AnalysisService(db)

    def capture_with_confirmation(
        self,
        target_name: str,
        candidate_id: str,
        exposure_seconds: float,
        filter_name: str = "L",
        binning: int = 1,
        confirmation_exposure_seconds: float = 5.0,
        confirmation_binning: int = 2,
        confirmation_max_attempts: int = 3,
        centering_tolerance_arcsec: float = 120.0,
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
            centering_tolerance_arcsec: Max allowed offset for confirmation

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

        # Step 1: Get fresh ephemeris from Horizons
        try:
            ephemeris = self.horizons.get_current_position(candidate_id)
            predicted_ra = ephemeris["ra_deg"]
            predicted_dec = ephemeris["dec_deg"]
            logger.info(f"Horizons ephemeris: RA={predicted_ra:.6f}, Dec={predicted_dec:.6f}")
        except Exception as e:
            logger.error(f"Failed to query Horizons for {candidate_id}: {e}")
            return {
                "success": False,
                "error": f"Horizons query failed: {e}",
                "confirmation_attempts": 0,
            }

        # Step 2: Confirmation loop (up to 3 attempts)
        confirmation_attempts = 0
        final_ra = predicted_ra
        final_dec = predicted_dec

        for attempt in range(1, confirmation_max_attempts + 1):
            confirmation_attempts = attempt
            logger.info(f"Confirmation attempt {attempt}/{confirmation_max_attempts}")

            # Slew to predicted position
            try:
                logger.info(f"Slewing to RA={final_ra:.6f}, Dec={final_dec:.6f}")
                self.nina.slew(final_ra, final_dec)
                self.nina.wait_for_mount_ready(timeout=180.0)
            except Exception as e:
                logger.error(f"Slew failed on attempt {attempt}: {e}")
                if attempt == confirmation_max_attempts:
                    return {
                        "success": False,
                        "error": f"Slew failed: {e}",
                        "confirmation_attempts": attempt,
                    }
                continue

            # Capture confirmation image
            try:
                confirm_target_name = f"{target_name}-CONFIRM"
                logger.info(f"Capturing confirmation image: {confirm_target_name}")
                self.nina.start_exposure(
                    filter_name="L",
                    binning=confirmation_binning,
                    exposure_seconds=confirmation_exposure_seconds,
                    target=confirm_target_name,
                    request_solve=False,  # Never rely on NINA solving
                )
            except Exception as e:
                logger.error(f"Confirmation capture failed on attempt {attempt}: {e}")
                if attempt == confirmation_max_attempts:
                    return {
                        "success": False,
                        "error": f"Confirmation capture failed: {e}",
                        "confirmation_attempts": attempt,
                    }
                continue

            # Wait for confirmation FITS file
            confirm_path = poll_for_fits_file(
                target_name=confirm_target_name,
                fits_directory=settings.nina_images_path,
                timeout=30.0,
            )
            if not confirm_path:
                logger.error(f"Confirmation FITS file not found on attempt {attempt}")
                if attempt == confirmation_max_attempts:
                    return {
                        "success": False,
                        "error": "Confirmation image not created",
                        "confirmation_attempts": attempt,
                    }
                continue

            # Wait for file write to complete
            if not wait_for_file_size_stable(confirm_path, stable_duration=1.0, timeout=10.0):
                logger.warning("Confirmation file size did not stabilize, continuing anyway")

            # Solve confirmation image
            try:
                logger.info(f"Solving confirmation image: {confirm_path}")
                solve_result = self.solver.solve_field(
                    fits_path=confirm_path,
                    ra_hint_deg=final_ra,
                    dec_hint_deg=final_dec,
                )
                solved_ra = solve_result["solution"]["ra_deg"]
                solved_dec = solve_result["solution"]["dec_deg"]
                logger.info(f"Confirmation solved: RA={solved_ra:.6f}, Dec={solved_dec:.6f}")
            except Exception as e:
                logger.error(f"Confirmation solve failed on attempt {attempt}: {e}")
                if attempt == confirmation_max_attempts:
                    return {
                        "success": False,
                        "error": f"Confirmation solve failed: {e}",
                        "confirmation_attempts": attempt,
                    }
                continue

            # Calculate offset
            offset_arcsec = self._calculate_separation_arcsec(
                predicted_ra, predicted_dec, solved_ra, solved_dec
            )
            logger.info(f"Confirmation offset: {offset_arcsec:.1f} arcsec (tolerance: {centering_tolerance_arcsec})")

            # Check if centered
            if offset_arcsec <= centering_tolerance_arcsec:
                logger.info(f"✓ Centered after {attempt} attempt(s), offset={offset_arcsec:.1f}\"")
                # Update final position to solved position for main exposure
                final_ra = solved_ra
                final_dec = solved_dec
                break

            # Not centered - update position for next attempt
            logger.warning(
                f"Not centered: offset={offset_arcsec:.1f}\" > {centering_tolerance_arcsec}\". "
                f"Re-slewing to solved position for next attempt."
            )
            final_ra = solved_ra
            final_dec = solved_dec

            if attempt == confirmation_max_attempts:
                return {
                    "success": False,
                    "error": f"Failed to center after {confirmation_max_attempts} attempts (final offset={offset_arcsec:.1f}\")",
                    "confirmation_attempts": attempt,
                    "predicted_ra_deg": predicted_ra,
                    "predicted_dec_deg": predicted_dec,
                    "solved_ra_deg": solved_ra,
                    "solved_dec_deg": solved_dec,
                }

        # Step 3: Create capture record for main exposure
        capture = CaptureLog(
            target=target_name,
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
            logger.info(f"Capturing main science image: {target_name}")
            self.nina.start_exposure(
                filter_name=filter_name,
                binning=binning,
                exposure_seconds=exposure_seconds,
                target=target_name,
                request_solve=False,  # Never rely on NINA solving
            )
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
        fits_path = poll_for_fits_file(
            target_name=target_name,
            fits_directory=settings.nina_images_path,
            timeout=exposure_seconds + 60.0,  # Exposure time + buffer
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
            solve_result = self.solver.solve_field(
                fits_path=fits_path,
                ra_hint_deg=final_ra,
                dec_hint_deg=final_dec,
            )
            capture.has_wcs = True
            capture.solved_ra_deg = solve_result["solution"]["ra_deg"]
            capture.solved_dec_deg = solve_result["solution"]["dec_deg"]
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
                capture_id=capture.id,
                tolerance_arcsec=10.0,
            )

            if association:
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


__all__ = ["SequentialCaptureService"]
