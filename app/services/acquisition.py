"""Two-stage target acquisition for NEOCP objects."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime

from app.core.config import settings
from app.services.nina_client import NinaBridgeService
from app.services.prediction import EphemerisPredictionService
from app.services.session import SESSION_STATE

logger = logging.getLogger(__name__)


@dataclass
class AcquisitionResult:
    """Result of two-stage acquisition attempt."""

    success: bool
    predicted_ra_deg: float
    predicted_dec_deg: float
    solved_ra_deg: float | None = None
    solved_dec_deg: float | None = None
    offset_arcsec: float | None = None
    verification_exposure_path: str | None = None
    refine_attempted: bool = False
    message: str = ""


class TwoStageAcquisition:
    """Implements two-stage slew-and-confirm acquisition.

    Workflow:
    1. Predict current position using Horizons ephemerides
    2. Slew to predicted coordinates
    3. Take short confirmation exposure (5-10s, binned for speed)
    4. Plate solve confirmation image
    5. Compare solved position vs prediction
    6. If offset > threshold: refine pointing and retry
    7. Return acquisition result
    """

    # Configuration
    CONFIRMATION_EXPOSURE_SECONDS = 8.0  # Short test exposure
    CONFIRMATION_BINNING = 2  # Higher binning for speed
    MAX_OFFSET_ARCSEC = 120.0  # 2 arcmin tolerance before refinement
    MAX_REFINE_ATTEMPTS = 2

    def __init__(self, bridge: NinaBridgeService, predictor: EphemerisPredictionService):
        """Initialize acquisition service.

        Args:
            bridge: NINA bridge service for equipment control
            predictor: Ephemeris prediction service
        """
        self.bridge = bridge
        self.predictor = predictor

    def acquire_target(
        self,
        candidate_id: str | None,
        target_name: str,
        filter_name: str = "R",
        binning: int | None = None,
    ) -> AcquisitionResult:
        """Execute two-stage acquisition sequence.

        Args:
            candidate_id: Database ID of NeoCandidate (None for synthetic targets)
            target_name: Target name for logging
            filter_name: Filter to use for confirmation exposure
            binning: Binning for confirmation exposure (default: CONFIRMATION_BINNING)

        Returns:
            AcquisitionResult with success status and details
        """

        binning = binning or self.CONFIRMATION_BINNING

        # Stage 1: Predict and slew
        SESSION_STATE.log_event(
            f"Acquisition Stage 1: Predicting position for {target_name}",
            "info",
        )

        predicted_coords = self.predictor.predict(
            candidate_id=candidate_id,
            when=datetime.utcnow(),
        )

        if not predicted_coords:
            return AcquisitionResult(
                success=False,
                predicted_ra_deg=0.0,
                predicted_dec_deg=0.0,
                message="Failed to predict target position",
            )

        ra_pred, dec_pred = predicted_coords

        SESSION_STATE.log_event(
            f"Acquisition: Slewing to predicted RA {ra_pred:.5f}°, Dec {dec_pred:.5f}°",
            "info",
        )

        try:
            self.bridge.slew(ra_pred, dec_pred)
            self.bridge.wait_for_mount_ready()
            self.bridge.wait_for_camera_idle()
        except Exception as exc:
            logger.error("Acquisition slew failed for %s: %s", target_name, exc)
            return AcquisitionResult(
                success=False,
                predicted_ra_deg=ra_pred,
                predicted_dec_deg=dec_pred,
                message=f"Slew failed: {exc}",
            )

        # Stage 2: Confirmation exposure
        SESSION_STATE.log_event(
            f"Acquisition Stage 2: Taking confirmation exposure ({self.CONFIRMATION_EXPOSURE_SECONDS}s, bin{binning})",
            "info",
        )

        try:
            result = self.bridge.start_exposure(
                filter_name=filter_name,
                binning=binning,
                exposure_seconds=self.CONFIRMATION_EXPOSURE_SECONDS,
                target=f"{target_name}_ACQ",
            )
        except Exception as exc:
            logger.error("Acquisition exposure failed for %s: %s", target_name, exc)
            return AcquisitionResult(
                success=False,
                predicted_ra_deg=ra_pred,
                predicted_dec_deg=dec_pred,
                message=f"Confirmation exposure failed: {exc}",
            )

        if not isinstance(result, dict):
            return AcquisitionResult(
                success=False,
                predicted_ra_deg=ra_pred,
                predicted_dec_deg=dec_pred,
                message="Invalid confirmation exposure result",
            )

        # Stage 3: Verify plate solve
        platesolve = result.get("platesolve")
        # Note: NINA API does not return file paths - will be None
        file_path = result.get("file")

        if not platesolve or not platesolve.get("Success"):
            return AcquisitionResult(
                success=False,
                predicted_ra_deg=ra_pred,
                predicted_dec_deg=dec_pred,
                verification_exposure_path=file_path,
                message="Confirmation exposure failed to solve",
            )

        coords = platesolve.get("Coordinates") or {}
        ra_solved = coords.get("RADegrees")
        dec_solved = coords.get("DECDegrees")

        if ra_solved is None or dec_solved is None:
            return AcquisitionResult(
                success=False,
                predicted_ra_deg=ra_pred,
                predicted_dec_deg=dec_pred,
                verification_exposure_path=file_path,
                message="Plate solve missing coordinates",
            )

        # Stage 4: Calculate offset
        offset_arcsec = self._calculate_offset(ra_pred, dec_pred, ra_solved, dec_solved)

        SESSION_STATE.log_event(
            f"Acquisition: Offset = {offset_arcsec:.1f}\" (predicted vs solved)",
            "info" if offset_arcsec < self.MAX_OFFSET_ARCSEC else "warn",
        )

        # Stage 5: Refine if needed
        if offset_arcsec > self.MAX_OFFSET_ARCSEC:
            SESSION_STATE.log_event(
                f"Acquisition: Offset exceeds {self.MAX_OFFSET_ARCSEC}\" threshold, refining pointing",
                "warn",
            )

            # Attempt refinement (slew to solved position)
            try:
                self.bridge.slew(ra_solved, dec_solved)
                self.bridge.wait_for_mount_ready()

                return AcquisitionResult(
                    success=True,
                    predicted_ra_deg=ra_pred,
                    predicted_dec_deg=dec_pred,
                    solved_ra_deg=ra_solved,
                    solved_dec_deg=dec_solved,
                    offset_arcsec=offset_arcsec,
                    verification_exposure_path=file_path,
                    refine_attempted=True,
                    message=f"Acquisition refined (offset was {offset_arcsec:.1f}\")",
                )
            except Exception as exc:
                logger.error("Acquisition refinement slew failed for %s: %s", target_name, exc)
                return AcquisitionResult(
                    success=False,
                    predicted_ra_deg=ra_pred,
                    predicted_dec_deg=dec_pred,
                    solved_ra_deg=ra_solved,
                    solved_dec_deg=dec_solved,
                    offset_arcsec=offset_arcsec,
                    verification_exposure_path=file_path,
                    refine_attempted=True,
                    message=f"Refinement slew failed: {exc}",
                )

        # Success - offset within tolerance
        return AcquisitionResult(
            success=True,
            predicted_ra_deg=ra_pred,
            predicted_dec_deg=dec_pred,
            solved_ra_deg=ra_solved,
            solved_dec_deg=dec_solved,
            offset_arcsec=offset_arcsec,
            verification_exposure_path=file_path,
            message=f"Acquisition successful (offset {offset_arcsec:.1f}\")",
        )

    def _calculate_offset(
        self, ra1: float, dec1: float, ra2: float, dec2: float
    ) -> float:
        """Calculate angular separation in arcseconds using haversine formula.

        Args:
            ra1, dec1: First position (degrees)
            ra2, dec2: Second position (degrees)

        Returns:
            Angular separation in arcseconds
        """

        # Convert to radians
        ra1_rad = math.radians(ra1)
        dec1_rad = math.radians(dec1)
        ra2_rad = math.radians(ra2)
        dec2_rad = math.radians(dec2)

        # Haversine formula
        delta_ra = ra2_rad - ra1_rad
        delta_dec = dec2_rad - dec1_rad

        a = (
            math.sin(delta_dec / 2) ** 2
            + math.cos(dec1_rad) * math.cos(dec2_rad) * math.sin(delta_ra / 2) ** 2
        )
        c = 2 * math.asin(math.sqrt(a))

        # Convert radians to arcseconds
        return math.degrees(c) * 3600.0


__all__ = ["TwoStageAcquisition", "AcquisitionResult"]
