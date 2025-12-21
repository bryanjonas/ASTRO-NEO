"""Simplified automation service for NEOCP target observation.

This module has been simplified as part of the minimum_func architecture:
- No weather checking (not app's concern)
- No equipment status checking (not app's concern)
- No user-configurable imaging parameters (preset-only)
- No in-memory SESSION_STATE (DB is single source of truth)
- Uses SequentialCaptureService for all captures
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.db.session import get_session
from app.services.equipment import get_active_equipment_profile
from app.services.motion import estimate_motion_rate_arcsec_per_min
from app.services.prediction import EphemerisPredictionService
from app.services.presets import select_preset
from app.services.sequential_capture import SequentialCaptureService

logger = logging.getLogger(__name__)


@dataclass
class TargetPlan:
    """Plan for observing a single target."""
    name: str
    candidate_id: str
    ra_deg: float
    dec_deg: float
    filter_name: str
    binning: int
    exposure_seconds: float
    count: int
    vmag: float | None = None
    motion_rate_arcsec_min: float | None = None


class AutomationService:
    """Simplified automation service using sequential capture."""

    def __init__(self, db_session=None):
        self.db = db_session

    def build_target_plan(
        self,
        target: dict[str, Any],
    ) -> TargetPlan:
        """
        Build a TargetPlan from a target candidate using presets.

        Args:
            target: Dictionary with keys: name, candidate_id, ra_deg, dec_deg, vmag (optional)

        Returns:
            TargetPlan with preset-determined imaging parameters
        """
        # Get equipment profile for preset selection
        profile = None
        try:
            profile = get_active_equipment_profile()
        except Exception:
            logger.warning("Could not load equipment profile, using defaults")

        # Get current ephemeris prediction if available
        ra_deg = target["ra_deg"]
        dec_deg = target["dec_deg"]
        candidate_id = target.get("candidate_id")

        if candidate_id:
            try:
                with get_session() as session:
                    predictor = EphemerisPredictionService(session)
                    now = datetime.utcnow()
                    predicted_coords = predictor.predict(candidate_id, now)
                    if predicted_coords:
                        ra_deg, dec_deg = predicted_coords
                        logger.info(f"Using predicted coordinates for {target['name']}: RA={ra_deg:.6f}, Dec={dec_deg:.6f}")
            except Exception as e:
                logger.warning(f"Could not get predicted coordinates: {e}")

        # Estimate motion rate for preset selection
        motion_rate = None
        if candidate_id:
            try:
                with get_session() as session:
                    motion_rate = estimate_motion_rate_arcsec_per_min(session, candidate_id)
            except Exception as e:
                logger.warning(f"Could not estimate motion rate: {e}")

        # Calculate urgency from score if available
        urgency = None
        score = target.get("score")
        if score is not None:
            urgency = max(0.0, min(1.0, score / 100.0))

        # Select preset based on vmag, urgency, and motion rate
        preset = select_preset(
            target.get("vmag"),
            profile=profile,
            urgency=urgency,
            motion_rate_arcsec_min=motion_rate,
            pixel_scale_arcsec_per_pixel=settings.astrometry_pixel_scale_arcsec,
        )

        target_name = (
            target.get("name")
            or target.get("trksub")
            or target.get("candidate_id")
            or "Unknown"
        )

        return TargetPlan(
            name=target_name,
            candidate_id=candidate_id,
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            filter_name=preset.filter,
            binning=preset.binning,
            exposure_seconds=preset.exposure_seconds,
            count=preset.count,
            vmag=target.get("vmag"),
            motion_rate_arcsec_min=motion_rate,
        )

    def execute_target_plan(
        self,
        plan: TargetPlan,
    ) -> dict[str, Any]:
        """
        Execute a target plan using sequential capture.

        Args:
            plan: TargetPlan to execute

        Returns:
            Dictionary with execution results:
                - target_name: str
                - started_at: str (ISO format)
                - completed_at: str (ISO format)
                - total_attempts: int
                - successful_captures: int
                - successful_associations: int
                - results: list of capture results
        """
        logger.info(
            f"Executing plan for {plan.name}: {plan.count}x{plan.exposure_seconds}s "
            f"@ {plan.filter_name}, binning {plan.binning}"
        )

        started_at = datetime.utcnow()
        results = []

        # Create sequential capture service
        with get_session() as session:
            capture_service = SequentialCaptureService(db=session)

            # Execute each exposure in the plan
            for i in range(plan.count):
                logger.info(f"Starting exposure {i+1}/{plan.count} for {plan.name}")

                try:
                    result = capture_service.capture_with_confirmation(
                        target_name=plan.name,
                        candidate_id=plan.candidate_id,
                        exposure_seconds=plan.exposure_seconds,
                        filter_name=plan.filter_name,
                        binning=plan.binning,
                        confirmation_exposure_seconds=5.0,
                        confirmation_binning=2,
                        confirmation_max_attempts=3,
                        centering_tolerance_arcsec=120.0,
                    )
                    results.append(result)

                    if result["success"]:
                        logger.info(
                            f"✓ Exposure {i+1}/{plan.count} successful: "
                            f"capture_id={result.get('capture_id')}, "
                            f"solved={result.get('solved')}, "
                            f"association={result.get('association_id') is not None}"
                        )
                    else:
                        logger.error(
                            f"✗ Exposure {i+1}/{plan.count} failed: {result.get('error')}"
                        )
                except Exception as e:
                    logger.error(f"Exception during exposure {i+1}/{plan.count}: {e}", exc_info=True)
                    results.append({
                        "success": False,
                        "error": str(e),
                        "confirmation_attempts": 0,
                    })

        completed_at = datetime.utcnow()

        # Calculate statistics
        successful_captures = sum(1 for r in results if r.get("success"))
        successful_associations = sum(1 for r in results if r.get("association_id") is not None)

        logger.info(
            f"Completed plan for {plan.name}: {successful_captures}/{len(results)} successful, "
            f"{successful_associations} with associations"
        )

        return {
            "target_name": plan.name,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "total_attempts": len(results),
            "successful_captures": successful_captures,
            "successful_associations": successful_associations,
            "results": results,
        }


__all__ = ["TargetPlan", "AutomationService"]
