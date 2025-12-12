"""Process images from NINA sequences - monitor, plate solve, and record metadata."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session
from app.models import AstrometricSolution, CaptureLog
from app.services.astrometry import AstrometryService
from app.services.image_monitor import ImageFileInfo, ImageMonitor
from app.services.session import SESSION_STATE

logger = logging.getLogger(__name__)


@dataclass
class SequenceResult:
    """Results from processing a sequence."""
    targets_processed: int
    images_received: int
    images_solved: int
    images_failed: int
    duration_seconds: float


class SequenceProcessor:
    """Process images from NINA sequences."""

    def __init__(self, session: Session | None = None):
        self.session = session
        self.monitor = ImageMonitor(settings.nina_images_path)
        self.astrometry = AstrometryService(session)

    def process_sequence(
        self,
        targets: list[dict[str, Any]],
        timeout_seconds: float = 1800.0,  # 30 minutes default
        poll_interval: float = 3.0,
    ) -> SequenceResult:
        """
        Process a single-target sequence by monitoring for images and solving them.

        NOTE: This function expects a single target in the targets list.
        For multiple targets, call this function once per target sequentially.

        Args:
            targets: List containing ONE target dict with keys: name, ra_deg, dec_deg, count
            timeout_seconds: Maximum time to wait for all images from this target
            poll_interval: How often to check for new images

        Returns:
            SequenceResult with processing statistics
        """
        if not targets:
            raise ValueError("At least one target must be provided")

        if len(targets) > 1:
            logger.warning(f"process_sequence received {len(targets)} targets but only processes the first one")

        start_time = datetime.utcnow()
        # Only process first target
        target = targets[0]
        target_name = target["name"]
        expected_count = target.get("count", 1)

        SESSION_STATE.log_event(
            f"Monitoring for {expected_count} images from {target_name}",
            "info"
        )

        # Watch for images from this single target
        images_by_target = self.monitor.watch_for_sequence(
            expected_targets=[target_name],
            timeout_seconds=timeout_seconds,
            poll_interval=poll_interval
        )

        # Process images for this target
        images = images_by_target.get(target_name, [])
        images_received = len(images)
        images_solved = 0
        images_failed = 0

        ra_hint = target.get("ra_deg")
        dec_hint = target.get("dec_deg")

        if images_received == 0:
            SESSION_STATE.log_event(
                f"⚠ No images received for {target_name} within {timeout_seconds/60:.1f}m timeout",
                "warn"
            )
        else:
            SESSION_STATE.log_event(
                f"Processing {images_received}/{expected_count} images for {target_name}",
                "info" if images_received == expected_count else "warn"
            )

        for idx, image in enumerate(images, 1):
            SESSION_STATE.add_capture(
                {
                    "kind": "sequence",
                    "target": target_name,
                    "sequence": target.get("sequence_name") or target_name,
                    "index": image.frame_number or idx,
                    "started_at": datetime.utcnow().isoformat(),
                    "path": str(image.path),
                    "predicted_ra_deg": ra_hint,
                    "predicted_dec_deg": dec_hint,
                }
            )
            # Check if NINA already solved this image
            nina_solved = self._check_nina_solve(image)

            if nina_solved:
                images_solved += 1
                SESSION_STATE.log_event(
                    f"✓ {target_name} #{idx}/{expected_count}: NINA solved (RA/Dec in FITS header)",
                    "good"
                )
            else:
                # Queue for local astrometry solving
                SESSION_STATE.log_event(
                    f"⚙ {target_name} #{idx}/{expected_count}: Running local plate solver (RA hint: {ra_hint:.2f}°, Dec hint: {dec_hint:.2f}°)",
                    "info"
                )

                success = self._solve_locally(
                    image,
                    ra_hint=ra_hint,
                    dec_hint=dec_hint
                )

                if success:
                    images_solved += 1
                    SESSION_STATE.log_event(
                        f"✓ {target_name} #{idx}/{expected_count}: Local astrometry.net solved successfully",
                        "good"
                    )
                else:
                    images_failed += 1
                    SESSION_STATE.log_event(
                        f"✗ {target_name} #{idx}/{expected_count}: Plate solve failed - check image quality or hints",
                        "warn"
                    )

        duration = (datetime.utcnow() - start_time).total_seconds()

        # Summary with percentage
        solve_rate = (images_solved / images_received * 100) if images_received > 0 else 0

        SESSION_STATE.log_event(
            f"Target {target_name} complete: {images_solved}/{images_received} images solved ({solve_rate:.0f}%) in {duration:.1f}s",
            "good" if images_failed == 0 else "warn"
        )

        return SequenceResult(
            targets_processed=1,  # Always 1 target per call
            images_received=images_received,
            images_solved=images_solved,
            images_failed=images_failed,
            duration_seconds=duration
        )

    def _check_nina_solve(self, image: ImageFileInfo) -> bool:
        """
        Check if NINA already plate-solved this image.

        NINA writes WCS headers directly to the FITS file if solve succeeds.
        We check for the presence of WCS keywords in the FITS header.
        """
        try:
            from astropy.io import fits as astropy_fits

            with astropy_fits.open(str(image.path)) as hdul:
                header = hdul[0].header
                # Check for WCS keywords that indicate a successful solve
                has_wcs = all(key in header for key in ["CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2"])

                if has_wcs:
                    # Record this in the database
                    self._record_nina_solve(image, header)
                    return True

        except Exception as e:
            logger.warning(f"Error checking NINA solve for {image.path}: {e}")

        return False

    def _record_nina_solve(self, image: ImageFileInfo, header: Any) -> None:
        """Record a NINA plate solve result in the database."""
        def _save(db: Session) -> None:
            # Check if we already have a solution for this image
            existing = db.exec(
                select(AstrometricSolution).where(
                    AstrometricSolution.path == str(image.path)
                )
            ).first()

            if existing:
                logger.debug(f"Solution already exists for {image.path}")
                return

            # Create astrometric solution record
            solution = AstrometricSolution(
                target=image.target,
                path=str(image.path),
                ra_deg=header.get("CRVAL1"),
                dec_deg=header.get("CRVAL2"),
                orientation_deg=header.get("CROTA2"),
                pixel_scale_arcsec=header.get("CDELT1", 0) * 3600.0 if header.get("CDELT1") else None,
                success=True,
                solver_info='{"source": "NINA"}',
                duration_seconds=0.0,
            )

            db.add(solution)
            db.commit()
            logger.info(f"Recorded NINA solve for {image.path.name}")

        if self.session:
            _save(self.session)
        else:
            with get_session() as db:
                _save(db)

    def _solve_locally(
        self,
        image: ImageFileInfo,
        ra_hint: float | None = None,
        dec_hint: float | None = None,
    ) -> bool:
        """
        Solve an image locally using astrometry.net.

        Returns True if solve succeeded, False otherwise.
        """
        try:
            # Create a CaptureLog entry if it doesn't exist
            def _ensure_capture(db: Session) -> CaptureLog:
                existing = db.exec(
                    select(CaptureLog).where(CaptureLog.path == str(image.path))
                ).first()

                if existing:
                    return existing

                capture = CaptureLog(
                    target=image.target,
                    filter=image.filter,
                    exposure_seconds=image.exposure_seconds,
                    path=str(image.path),
                    timestamp=datetime.utcnow(),
                )
                db.add(capture)
                db.commit()
                db.refresh(capture)
                return capture

            if self.session:
                capture = _ensure_capture(self.session)
            else:
                with get_session() as db:
                    capture = _ensure_capture(db)

            # Run astrometry solve
            solution = self.astrometry.solve_capture(
                capture_id=capture.id,
                ra_hint=ra_hint,
                dec_hint=dec_hint,
                radius_deg=5.0,  # Search within 5 degrees
            )

            return solution.success

        except Exception as e:
            logger.error(f"Error solving {image.path}: {e}", exc_info=True)
            return False


__all__ = ["SequenceProcessor", "SequenceResult"]
