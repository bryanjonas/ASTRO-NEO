"""Control loop utilities for sequential single-target exposures."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.db.session import get_session
from app.services.acquisition import TwoStageAcquisition
from app.services.nina_client import NinaBridgeService
from app.services.prediction import EphemerisPredictionService
from app.services.session import SESSION_STATE

logger = logging.getLogger(__name__)


@dataclass
class CaptureTargetDescriptor:
    """Descriptor describing the required inputs for the capture loop."""

    name: str
    candidate_id: str | None
    ra_deg: float
    dec_deg: float
    filter_name: str
    binning: int
    exposure_seconds: float
    count: int
    sequence_name: str | None = None


@dataclass
class CaptureLoopResult:
    """Summary of how many exposures were captured/solved for a target."""

    target: str
    exposures_attempted: int
    exposures_solved: int
    exposures_failed: int


def run_capture_loop(
    descriptor: CaptureTargetDescriptor,
    bridge: NinaBridgeService,
    use_two_stage_acquisition: bool = True,
) -> CaptureLoopResult:
    """Run a guided exposure loop for a single target, recomputing coordinates each iteration.

    Args:
        descriptor: Target and exposure configuration
        bridge: NINA bridge service
        use_two_stage_acquisition: If True, perform two-stage acquisition before exposure loop

    Returns:
        CaptureLoopResult with success/failure counts
    """

    solved = 0
    failed = 0
    attempted = 0

    # Optional: Two-stage acquisition before main exposures
    if use_two_stage_acquisition and descriptor.candidate_id:
        SESSION_STATE.log_event(
            f"Performing two-stage acquisition for {descriptor.name}",
            "info",
        )

        with get_session() as session:
            predictor = EphemerisPredictionService(session)
            acquisition = TwoStageAcquisition(bridge, predictor)

            acq_result = acquisition.acquire_target(
                candidate_id=descriptor.candidate_id,
                target_name=descriptor.name,
                filter_name=descriptor.filter_name,
            )

        if not acq_result.success:
            SESSION_STATE.log_event(
                f"Two-stage acquisition failed for {descriptor.name}: {acq_result.message}",
                "error",
            )
            # Don't abort - continue with normal slew-per-exposure workflow
            SESSION_STATE.log_event(
                "Continuing with standard per-exposure slew workflow",
                "warn",
            )
        else:
            SESSION_STATE.log_event(
                f"Acquisition successful: {acq_result.message}",
                "good",
            )

    for idx in range(1, descriptor.count + 1):
        # Check if session was paused or ended
        current_session = SESSION_STATE.current
        if not current_session:
            SESSION_STATE.log_event(
                f"Session ended - stopping capture loop for {descriptor.name}",
                "warn",
            )
            break

        # Check session status via database to get real-time state
        with get_session() as db_session:
            from app.models.session import ObservingSession as DBObservingSession
            from sqlmodel import select
            db_obs_session = db_session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()

            if not db_obs_session:
                SESSION_STATE.log_event(
                    f"Session ended - stopping capture loop for {descriptor.name}",
                    "warn",
                )
                break

            if db_obs_session.status == "paused":
                SESSION_STATE.log_event(
                    f"Session paused - stopping capture loop for {descriptor.name}",
                    "warn",
                )
                break

        attempt_time = datetime.utcnow()
        ra, dec = _predict_current_coords(
            descriptor.candidate_id,
            descriptor.ra_deg,
            descriptor.dec_deg,
            attempt_time,
        )
        SESSION_STATE.log_event(
            f"Exposure {idx}/{descriptor.count}: Predicted position RA {ra:.5f}°, Dec {dec:.5f}°",
            "info",
        )

        # Step 1: Slew to predicted position
        try:
            bridge.slew(ra, dec)
            bridge.wait_for_mount_ready()
            bridge.wait_for_camera_idle()
        except Exception as exc:
            logger.error("Failed to slew/wait for %s (exposure %d/%d): %s", descriptor.name, idx, descriptor.count, exc)
            SESSION_STATE.log_event(
                f"Slew/mount preparation failed for {descriptor.name} (exposure {idx}/{descriptor.count}): {exc}",
                "error",
            )
            failed += 1
            continue

        # Step 2: Take short confirmation exposure to verify pointing
        SESSION_STATE.log_event(
            f"Exposure {idx}/{descriptor.count}: Taking confirmation exposure...",
            "info",
        )

        try:
            # Short confirmation exposure (5s, bin2) for fast plate solving
            confirm_exposure = 5.0

            confirmation_result = bridge.start_exposure(
                filter_name=descriptor.filter_name,
                binning=2,  # Use bin2 for faster download and plate solve
                exposure_seconds=confirm_exposure,
                target=f"{descriptor.name}-CONFIRM",
                request_solve=True,
            )
        except Exception as exc:
            logger.error("Confirmation exposure failed for %s (exposure %d/%d): %s", descriptor.name, idx, descriptor.count, exc)
            SESSION_STATE.log_event(
                f"Confirmation exposure FAILED for {descriptor.name} (exposure {idx}/{descriptor.count}): {exc}",
                "warn",
            )
            # Continue anyway - we'll rely on the main exposure's plate solve
        else:
            # Step 3: Check confirmation solve and re-slew if needed
            if isinstance(confirmation_result, dict):
                confirm_platesolve = confirmation_result.get("platesolve")
                if confirm_platesolve and confirm_platesolve.get("Success"):
                    coords = confirm_platesolve.get("Coordinates") or {}
                    ra_solved = coords.get("RADegrees")
                    dec_solved = coords.get("DECDegrees")

                    if ra_solved is not None and dec_solved is not None:
                        # Calculate offset using haversine formula
                        from math import radians, cos, sin, asin, sqrt
                        dra = radians(ra_solved - ra)
                        ddec = radians(dec_solved - dec)
                        a = sin(ddec / 2) ** 2 + cos(radians(dec)) * cos(radians(dec_solved)) * sin(dra / 2) ** 2
                        offset_arcsec = 2 * asin(sqrt(a)) * 206264.806  # Convert radians to arcseconds

                        SESSION_STATE.log_event(
                            f"Confirmation solve: offset {offset_arcsec:.1f}\" from predicted position",
                            "info" if offset_arcsec < 120 else "warn",
                        )

                        # Step 4: Re-slew if offset exceeds threshold
                        if offset_arcsec > 120:
                            SESSION_STATE.log_event(
                                f"Offset exceeds 120\", re-slewing to solved position RA {ra_solved:.5f}°, Dec {dec_solved:.5f}°",
                                "info",
                            )
                            try:
                                bridge.slew(ra_solved, dec_solved)
                                bridge.wait_for_mount_ready()
                            except Exception as exc:
                                logger.error("Re-slew failed for %s: %s", descriptor.name, exc)
                                SESSION_STATE.log_event(f"Re-slew failed: {exc}", "warn")
                    else:
                        # Plate solve succeeded but missing coordinates
                        SESSION_STATE.log_event(
                            f"Confirmation solve succeeded but missing coordinates - continuing with predicted position",
                            "warn",
                        )
                elif confirm_platesolve:
                    # Plate solve attempted but failed
                    error_msg = confirm_platesolve.get("Error") or "Unknown error"
                    SESSION_STATE.log_event(
                        f"Confirmation plate solve failed: {error_msg} - continuing with predicted position",
                        "warn",
                    )
                else:
                    # No plate solve data returned
                    SESSION_STATE.log_event(
                        f"Confirmation exposure returned no plate solve data - continuing with predicted position",
                        "warn",
                    )
            else:
                # Unexpected response type
                SESSION_STATE.log_event(
                    f"Confirmation exposure returned unexpected response type - continuing with predicted position",
                    "warn",
                )

        # Step 5: Take the actual science exposure
        attempted += 1
        SESSION_STATE.log_event(
            f"Exposure {idx}/{descriptor.count}: Starting science exposure ({descriptor.exposure_seconds}s)...",
            "info",
        )

        science_solve_requested = False
        try:
            result = bridge.start_exposure(
                filter_name=descriptor.filter_name,
                binning=descriptor.binning,
                exposure_seconds=descriptor.exposure_seconds,
                target=descriptor.name,
                request_solve=science_solve_requested,
            )
        except Exception as exc:
            logger.error("Camera capture failed for %s (exposure %d/%d): %s", descriptor.name, idx, descriptor.count, exc)
            SESSION_STATE.log_event(
                f"Camera capture FAILED for {descriptor.name} (exposure {idx}/{descriptor.count}): {exc}",
                "error",
            )
            failed += 1
            # Do NOT retry the same exposure - continue to next one
            # This prevents infinite slew loops when NINA rejects captures
            continue

        if not isinstance(result, dict):
            logger.error("NINA capture returned unexpected payload type: %s", type(result))
            SESSION_STATE.log_event(
                f"Invalid capture response for {descriptor.name} (exposure {idx}/{descriptor.count})",
                "error",
            )
            failed += 1
            continue

        # Note: NINA API does not return file paths - will be None
        # File path will be filled by file system monitoring service
        file_path = result.get("file")
        platesolve = result.get("platesolve") if science_solve_requested else None

        started_at = datetime.utcnow().isoformat()
        capture_record: dict[str, Any] = {
            "kind": "exposure",
            "target": descriptor.name,
            "sequence": descriptor.sequence_name or descriptor.name,
            "index": idx,
            "started_at": started_at,
            "path": file_path or "",  # Empty initially, filled by file monitor
            "predicted_ra_deg": ra,
            "predicted_dec_deg": dec,
            "platesolve": platesolve,
            "exposure_seconds": descriptor.exposure_seconds,
            "filter": descriptor.filter_name,
            "binning": descriptor.binning,
        }
        SESSION_STATE.add_capture(capture_record)

        # Treat successful capture as solved pending local WCS
        solved += 1

        if platesolve:
            success = platesolve.get("Success")
            coords = (platesolve.get("Coordinates") or {})
            coord_str = ""
            ra_header = coords.get("RADegrees")
            dec_header = coords.get("DECDegrees")
            if isinstance(ra_header, (int, float)) and isinstance(dec_header, (int, float)):
                coord_str = f" RA {ra_header:.3f}° Dec {dec_header:.3f}°"
            if success:
                SESSION_STATE.log_event(
                    f"NINA solve succeeded for {descriptor.name}.{coord_str}",
                    "good",
                )
            else:
                SESSION_STATE.log_event(
                    f"NINA solve failed for {descriptor.name} (exposure {idx}/{descriptor.count}){coord_str}",
                    "warn",
                )
        else:
            SESSION_STATE.log_event(
                f"Local solver pending for {descriptor.name} (exposure {idx}/{descriptor.count})",
                "info",
            )

    SESSION_STATE.log_event(
        f"Target {descriptor.name} exposures complete: {solved}/{attempted} solved, {failed} failed",
        "good" if failed == 0 else "warn",
    )

    return CaptureLoopResult(
        target=descriptor.name,
        exposures_attempted=attempted,
        exposures_solved=solved,
        exposures_failed=failed,
    )


def _predict_current_coords(
    candidate_id: str | None,
    fallback_ra: float,
    fallback_dec: float,
    when: datetime,
) -> tuple[float, float]:
    if candidate_id:
        try:
            with get_session() as session:
                predictor = EphemerisPredictionService(session)
                predicted = predictor.predict(candidate_id, when)
        except Exception as exc:  # pragma: no cover - best-effort prediction
            logger.warning("Prediction failed for %s: %s", candidate_id, exc)
            predicted = None
        if predicted:
            return predicted
    return fallback_ra, fallback_dec
