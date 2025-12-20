"""Monitor NINA image directory for new FITS files and queue them for processing."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astropy.io import fits as astropy_fits
from sqlmodel import select

from app.core.config import settings
from app.services.session import SESSION_STATE

logger = logging.getLogger(__name__)

# NINA filename template: $$DATEMINUS12$$\$$TARGETNAME$$\$$IMAGETYPE$$\$$TARGETNAME$$_$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$
# Examples:
#   A11wdXf_2025-12-07_23-45-12_L_60.0s_001.fits (with filter)
#   ZTF109i_2025-12-19_20-28-04__102.00s_0000.fits (no filter, double underscore)

NINA_FILENAME_PATTERN = re.compile(
    r"^(?P<target>[^_]+)_"  # Target name (required)
    r"(?P<datetime>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})"  # DateTime
    r"(?:_(?P<filter>[^_]+))?_+"  # Optional filter (non-capturing group for the first underscore)
    r"(?P<exposure>[\d.]+)s_"  # Exposure time
    r"(?P<frame>\d+)"  # Frame number
    r"\.fits?$",  # Extension
    re.IGNORECASE
)


@dataclass
class ImageFileInfo:
    """Information extracted from a NINA image filename."""
    path: Path
    target: str
    datetime_str: str
    filter: str
    exposure_seconds: float
    frame_number: int
    image_type: str  # LIGHT, DARK, BIAS, FLAT
    has_wcs: bool = False
    timestamp: datetime | None = None


class ImageMonitor:
    """Monitor the NINA images directory for new FITS files."""

    def __init__(self, images_path: str | Path | None = None):
        self.images_path = Path(images_path or settings.nina_images_path or "/data/fits")
        self.seen_files: set[Path] = set()
        self.cached_files: dict[str, ImageFileInfo] = {}
        self.pending_solves: dict[str, dict[str, Any]] = {}
        self.last_scan_time = 0.0
        self.max_retry_attempts = 3
        self.retry_delay_seconds = 30.0

    def scan_for_new_images(self) -> list[ImageFileInfo]:
        """Scan the images directory for new FITS files since last scan."""
        if not self.images_path.exists():
            logger.warning(f"Images path does not exist: {self.images_path}")
            return []

        new_images: list[ImageFileInfo] = []

        # Scan for FITS files recursively
        for fits_file in self.images_path.rglob("*.fits"):
            try:
                image_info = self._parse_filename(fits_file)
            except Exception as exc:  # pragma: no cover - defensive parsing
                logger.debug(f"Unable to parse {fits_file}: {exc}")
                continue

            if not image_info:
                continue

            self.cached_files[str(fits_file)] = image_info

            if fits_file in self.seen_files:
                continue

            # Check if file was modified after last scan
            try:
                mtime = fits_file.stat().st_mtime
                if mtime < self.last_scan_time:
                    continue
            except OSError:
                continue

            new_images.append(image_info)
            self.seen_files.add(fits_file)
            logger.info(
                f"Detected new image: {image_info.target} "
                f"{image_info.filter} {image_info.exposure_seconds}s "
                f"frame #{image_info.frame_number} "
                f"(WCS: {'yes' if image_info.has_wcs else 'no'})"
            )

            # Correlate with SESSION_STATE captures and trigger processing
            self._correlate_and_process(image_info)

        self._backfill_unmatched_captures()
        self._process_pending_solves()

        self.last_scan_time = time.time()
        return new_images

    def _parse_filename(self, path: Path) -> ImageFileInfo | None:
        """Parse NINA filename to extract metadata."""
        # Extract image type from parent directory name
        image_type = "LIGHT"
        parent_name = path.parent.name.upper()
        if parent_name in ("LIGHT", "DARK", "BIAS", "FLAT", "SNAPSHOT"):
            # Treat SNAPSHOT frames as LIGHTs so automation can ingest NINA's
            # snapshot directories the same way as standard light frames.
            image_type = "LIGHT" if parent_name == "SNAPSHOT" else parent_name

        # Try to extract target from grandparent directory
        target = "Unknown"
        if len(path.parents) >= 2:
            target = path.parents[1].name

        match = NINA_FILENAME_PATTERN.match(path.name)
        if not match:
            logger.debug(f"Filename does not match NINA pattern: {path.name}")
            # Return basic info even if pattern doesn't match
            return ImageFileInfo(
                path=path,
                target=self._guess_target(path, target),
                datetime_str="",
                filter="",
                exposure_seconds=0.0,
                frame_number=0,
                image_type=image_type,
                has_wcs=self._check_wcs(path),
                timestamp=None
            )

        parsed_target = match.group("target")
        parsed_filter = match.group("filter")
        datetime_str = match.group("datetime")

        # Parse timestamp from filename
        timestamp = None
        try:
            timestamp = datetime.strptime(datetime_str, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            logger.warning(f"Could not parse timestamp from {datetime_str}")

        return ImageFileInfo(
            path=path,
            target=self._guess_target(path, parsed_target),
            datetime_str=datetime_str,
            filter=self._guess_filter(path, parsed_filter),
            exposure_seconds=float(match.group("exposure")),
            frame_number=int(match.group("frame")),
            image_type=image_type,
            has_wcs=self._check_wcs(path),
            timestamp=timestamp
        )

    def _guess_target(self, path: Path, parsed_target: str | None) -> str:
        if parsed_target:
            return parsed_target
        target = self._read_header_field(path, ("OBJECT", "TARGET", "OBJECT-NAME", "OBJECT_NAME"))
        if target:
            return target
        return "Unknown"

    def _guess_filter(self, path: Path, parsed_filter: str | None) -> str:
        if parsed_filter and parsed_filter.strip():
            return parsed_filter
        filter_name = self._read_header_field(path, ("FILTER", "FILTER_NAME"))
        if filter_name:
            return filter_name
        return ""

    def _read_header_field(self, path: Path, keys: tuple[str, ...]) -> str | None:
        try:
            with astropy_fits.open(str(path)) as hdul:
                header = hdul[0].header
                for key in keys:
                    value = header.get(key)
                    if value:
                        return str(value)
        except Exception as exc:
            logger.debug(f"Unable to read FITS header {keys} from {path.name}: {exc}")
        return None

    def _check_wcs(self, path: Path) -> bool:
        """Check if FITS file has WCS (plate solve) information."""
        try:
            with astropy_fits.open(str(path)) as hdul:
                header = hdul[0].header
                # Check for standard WCS keywords
                # CTYPE1/CTYPE2 indicate coordinate system type
                # CRVAL1/CRVAL2 indicate reference pixel values (RA/Dec)
                has_ctype = "CTYPE1" in header and "CTYPE2" in header
                has_crval = "CRVAL1" in header and "CRVAL2" in header

                # Additional check: CD matrix or CDELT keywords
                has_cd_matrix = ("CD1_1" in header and "CD2_2" in header)
                has_cdelt = ("CDELT1" in header and "CDELT2" in header)

                return has_ctype and has_crval and (has_cd_matrix or has_cdelt)
        except Exception as exc:
            logger.debug(f"Unable to check WCS in {path.name}: {exc}")
            return False

    def _correlate_and_process(self, image_info: ImageFileInfo) -> None:
        """Correlate detected image with SESSION_STATE captures and trigger processing."""
        # Skip non-LIGHT frames
        if image_info.image_type != "LIGHT":
            logger.debug(f"Skipping non-LIGHT frame: {image_info.path.name}")
            return

        # Skip confirmation exposures (contain -CONFIRM in target name)
        if "-CONFIRM" in image_info.target:
            logger.debug(f"Skipping confirmation exposure: {image_info.path.name}")
            return

        # Skip synthetic or test targets (FAKE-*)
        if image_info.target.upper().startswith("FAKE"):
            logger.debug(f"Skipping confirmation exposure: {image_info.path.name}")
            return

        # Get current session
        session = SESSION_STATE.current
        if not session:
            logger.warning(f"No active session to correlate image: {image_info.path.name}")
            return

        # Find matching capture record
        matching_capture = self._find_matching_capture(session.captures, image_info)

        if matching_capture:
            self._handle_matched_capture(image_info, matching_capture)
        else:
            logger.warning(
                f"No matching capture record for {image_info.path.name} "
                f"(target: {image_info.target}, exposure: {image_info.exposure_seconds}s, "
                f"timestamp: {image_info.timestamp})"
            )

    def _handle_matched_capture(self, image_info: ImageFileInfo, capture_record: dict[str, Any]) -> None:
        capture_record["path"] = str(image_info.path)
        self.seen_files.add(image_info.path)
        self._update_capture_path(capture_record, str(image_info.path))

        logger.info(
            f"Correlated {image_info.path.name} to capture "
            f"{capture_record.get('target')} index {capture_record.get('index')}"
        )
        SESSION_STATE.log_event(
            f"File detected: {image_info.target} frame {capture_record.get('index')}",
            "good"
        )

        if image_info.has_wcs:
            logger.info(f"Image {image_info.path.name} already has WCS, proceeding to processing")
            SESSION_STATE.log_event(
                f"Image {image_info.target} pre-solved by NINA",
                "info"
            )
            self._trigger_processing(image_info, capture_record)
        else:
            logger.info(f"Image {image_info.path.name} requires local plate solving (queued)")
            SESSION_STATE.log_event(
                f"Image {image_info.target} needs plate solving",
                "warn"
            )
            self._queue_pending_solve(image_info, capture_record)

    def _queue_pending_solve(self, image_info: ImageFileInfo, capture_record: dict[str, Any]) -> None:
        key = str(image_info.path)
        if key in self.pending_solves:
            return
        capture_record["solver_status"] = "pending"
        capture_record.pop("solver_error", None)
        self._update_capture_solver_status(capture_record, status="pending", error=None)
        self.pending_solves[key] = {
            "image_info": image_info,
            "capture": capture_record,
            "attempts": 0,
            "next_attempt": time.time(),
        }

    def _process_pending_solves(self, max_per_cycle: int = 2) -> None:
        if not self.pending_solves:
            return

        total_pending = len(self.pending_solves)
        logger.debug(f"Processing pending solves: {total_pending} in queue")

        now = time.time()
        processed = 0
        for path, payload in list(self.pending_solves.items()):
            if processed >= max_per_cycle:
                break
            if payload["next_attempt"] > now:
                wait_time = int(payload["next_attempt"] - now)
                logger.debug(f"Skipping {path}: retry in {wait_time}s")
                continue

            attempt_num = payload["attempts"] + 1
            logger.info(
                f"Attempting plate solve ({attempt_num}/{self.max_retry_attempts}) for {path}"
            )

            success, error_msg = self._trigger_plate_solving(payload["image_info"], payload["capture"])
            if success:
                logger.info(f"Plate solve succeeded for {path} after {attempt_num} attempt(s)")
                del self.pending_solves[path]
                payload["capture"]["solver_status"] = "solved"
                self._update_capture_solver_status(payload["capture"], status="solved", error=None)
            else:
                payload["attempts"] += 1
                payload["next_attempt"] = now + self.retry_delay_seconds
                if error_msg:
                    payload["last_error"] = error_msg

                if payload["attempts"] >= self.max_retry_attempts:
                    final_error = payload.get("last_error") or "Plate solve failed"
                    logger.warning(
                        f"Plate solve failed for {path} after {self.max_retry_attempts} attempts: {final_error}"
                    )
                    self._record_solver_failure(payload["capture"], final_error)
                    del self.pending_solves[path]
                else:
                    logger.info(
                        f"Plate solve attempt {payload['attempts']}/{self.max_retry_attempts} failed for {path}, "
                        f"will retry in {self.retry_delay_seconds}s: {error_msg}"
                    )
            processed += 1

        if total_pending > 0:
            remaining = len(self.pending_solves)
            logger.info(f"Pending solve queue: {remaining} remaining (processed {processed} this cycle)")

    def _record_solver_failure(self, capture_record: dict[str, Any], message: str) -> None:
        capture_record["solver_status"] = "error"
        capture_record["solver_error"] = message
        SESSION_STATE.log_event(
            f"Plate solve failed for {capture_record.get('target')} index {capture_record.get('index')}: {message}",
            "error"
        )
        self._update_capture_solver_status(capture_record, status="error", error=message)

    def _backfill_unmatched_captures(self) -> None:
        session = SESSION_STATE.current
        if not session or not session.captures:
            return
        unmatched = [cap for cap in session.captures if not cap.get("path")]
        if not unmatched or not self.cached_files:
            return

        for capture in unmatched:
            image_info = self._match_existing_file_to_capture(capture)
            if image_info:
                logger.info(
                    f"Backfilling unmatched capture {capture.get('target')} "
                    f"index {capture.get('index')} with {image_info.path.name}"
                )
                self._handle_matched_capture(image_info, capture)

    def _match_existing_file_to_capture(
        self,
        capture_record: dict[str, Any],
        timestamp_tolerance: float = 600.0,
        exposure_tolerance: float = 1.0,
    ) -> ImageFileInfo | None:
        target = capture_record.get("target")
        if not target:
            return None
        normalized_target = target.replace("-CONFIRM", "")
        capture_exposure = capture_record.get("exposure_seconds")
        capture_started_at = capture_record.get("started_at")
        if isinstance(capture_started_at, str):
            try:
                capture_started_at = datetime.fromisoformat(capture_started_at.replace("Z", "+00:00"))
            except ValueError:
                capture_started_at = None

        for info in self.cached_files.values():
            if info.image_type != "LIGHT":
                continue
            if info.target not in {target, normalized_target}:
                continue
            if capture_exposure and info.exposure_seconds:
                if abs(capture_exposure - info.exposure_seconds) > exposure_tolerance:
                    continue
            if capture_started_at and info.timestamp:
                diff = abs((info.timestamp - capture_started_at).total_seconds())
                if diff > timestamp_tolerance:
                    continue
            if self._capture_path_in_use(str(info.path)):
                continue
            return info
        return None

    def _capture_path_in_use(self, path: str) -> bool:
        session = SESSION_STATE.current
        if not session:
            return False
        return any(cap.get("path") == path for cap in session.captures)
    def _find_matching_capture(
        self,
        captures: list[dict[str, Any]],
        image_info: ImageFileInfo
    ) -> dict[str, Any] | None:
        """Find capture record that matches the image file.

        Matching criteria:
        - Target name match
        - Timestamp within ±30s tolerance
        - Exposure time within ±0.5s tolerance
        - Optional: frame number match
        """
        TIMESTAMP_TOLERANCE_SECONDS = 30
        EXPOSURE_TOLERANCE_SECONDS = 0.5

        if not image_info.timestamp:
            logger.debug("Image has no timestamp, cannot correlate by time")
            return None

        for capture in captures:
            # Skip if already has a path
            if capture.get("path"):
                continue

            # Check target name (remove -CONFIRM suffix if present)
            capture_target = capture.get("target", "")
            if capture_target.endswith("-CONFIRM"):
                continue
            if capture_target != image_info.target:
                continue

            # Check exposure time
            capture_exposure = capture.get("exposure_seconds")
            if capture_exposure is None:
                # Try to infer from other fields if needed
                continue
            if abs(capture_exposure - image_info.exposure_seconds) > EXPOSURE_TOLERANCE_SECONDS:
                continue

            # Check timestamp
            capture_time_str = capture.get("started_at")
            if not capture_time_str:
                continue

            try:
                if isinstance(capture_time_str, str):
                    capture_time = datetime.fromisoformat(capture_time_str.replace("Z", "+00:00"))
                else:
                    capture_time = capture_time_str

                time_diff = abs((image_info.timestamp - capture_time).total_seconds())
                if time_diff > TIMESTAMP_TOLERANCE_SECONDS:
                    continue
            except (ValueError, AttributeError) as exc:
                logger.debug(f"Could not parse capture timestamp {capture_time_str}: {exc}")
                continue

            # Optional: Check frame number/index
            # For now, we'll match the first suitable record
            return capture

        return None

    def _trigger_plate_solving(self, image_info: ImageFileInfo, capture_record: dict[str, Any]) -> tuple[bool, str | None]:
        """Trigger internal plate solving for an image that lacks WCS."""
        from app.services.solver import solve_fits

        try:
            # Extract RA/Dec hints from capture record if available
            ra_hint = capture_record.get("predicted_ra_deg")
            dec_hint = capture_record.get("predicted_dec_deg")

            logger.info(
                f"Triggering plate solve for {image_info.path} "
                f"with hints RA={ra_hint}, Dec={dec_hint}"
            )

            result = solve_fits(
                image_info.path,
                ra_hint=ra_hint,
                dec_hint=dec_hint,
                radius_deg=1.0  # Search within 1 degree
            )

            if result and result.get("solution"):
                SESSION_STATE.log_event(
                    f"Plate solve succeeded for {image_info.target}",
                    "good"
                )
                logger.info(f"Plate solve succeeded for {image_info.path.name}")

                # Re-check WCS in original FITS (solver should have written it back)
                image_info.has_wcs = self._check_wcs(image_info.path)

                # Update capture record with solver success
                capture_record["solver_status"] = "solved"
                capture_record.pop("solver_error", None)

                self._trigger_processing(image_info, capture_record)
                return True, None
            else:
                SESSION_STATE.log_event(
                    f"Plate solve failed for {image_info.target}",
                    "error"
                )
                logger.error(f"Plate solve failed for {image_info.path.name}")
                return False, "Plate solve returned no solution"
        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            SESSION_STATE.log_event(
                f"Plate solve error for {image_info.target}: {error_msg}",
                "error"
            )
            logger.error(
                f"Plate solve error for {image_info.path.name}: {error_msg}",
                exc_info=True  # Include full traceback in logs
            )
            return False, error_msg

    def _update_capture_path(self, capture_record: dict[str, Any], path: str) -> None:
        """Persist the file path update to the SESSION_STATE database."""
        from app.db.session import get_session
        from app.models.session import ObservingSession as DBObservingSession

        with get_session() as db_session:
            # Get the active session
            db_obs_session = db_session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()

            if not db_obs_session:
                logger.warning("No active session to update capture path")
                return

            # Update the captures list in stats
            stats = dict(db_obs_session.stats)
            captures = stats.get("captures", [])

            # Find and update the matching capture
            for cap in captures:
                if (cap.get("target") == capture_record.get("target") and
                    cap.get("index") == capture_record.get("index") and
                    not cap.get("path")):  # Only update if path is empty
                    cap["path"] = path
                    logger.debug(f"Updated capture {cap.get('target')} index {cap.get('index')} with path {path}")
                    break

            stats["captures"] = captures
            db_obs_session.stats = stats
            db_session.add(db_obs_session)
            db_session.commit()

    def _update_capture_solver_status(self, capture_record: dict[str, Any], status: str, error: str | None) -> None:
        """Persist solver status into observing session stats."""
        from app.db.session import get_session
        from app.models.session import ObservingSession as DBObservingSession

        with get_session() as db_session:
            db_obs_session = db_session.exec(
                select(DBObservingSession)
                .where(DBObservingSession.status != "ended")
                .order_by(DBObservingSession.start_time.desc())
            ).first()

            if not db_obs_session:
                return

            stats = dict(db_obs_session.stats)
            captures = stats.get("captures", [])
            for cap in captures:
                if cap.get("target") == capture_record.get("target") and cap.get("index") == capture_record.get("index"):
                    cap["solver_status"] = status
                    if error:
                        cap["solver_error"] = error
                    elif "solver_error" in cap:
                        cap.pop("solver_error")
                    break

            stats["captures"] = captures
            db_obs_session.stats = stats
            db_session.add(db_obs_session)
            db_session.commit()

    def _trigger_processing(self, image_info: ImageFileInfo, capture_record: dict[str, Any]) -> None:
        """Trigger downstream processing for a solved image."""
        from app.db.session import get_session
        from app.models import CaptureLog
        from app.services.analysis import AnalysisService

        logger.info(f"Image {image_info.path.name} ready for downstream processing")

        # Load WCS
        wcs_path = image_info.path.with_suffix(".wcs")
        if not wcs_path.exists():
            logger.warning(f"No WCS file found at {wcs_path}, skipping association")
            SESSION_STATE.log_event(
                f"Cannot process {image_info.target} - missing WCS",
                "warn"
            )
            return

        try:
            from astropy.wcs import WCS
            wcs = WCS(str(wcs_path))
        except Exception as e:
            logger.error(f"Failed to load WCS from {wcs_path}: {e}")
            SESSION_STATE.log_event(
                f"Cannot process {image_info.target} - WCS load failed",
                "error"
            )
            return

        # Get CaptureLog record
        with get_session() as session:
            capture = session.exec(
                select(CaptureLog).where(CaptureLog.path == str(image_info.path))
            ).first()

            if not capture:
                logger.warning(f"No CaptureLog found for {image_info.path}")
                SESSION_STATE.log_event(
                    f"Cannot associate {image_info.target} - no capture record",
                    "warn"
                )
                return

            # Run association with star subtraction
            logger.info(f"Running auto-association for {image_info.target}")
            SESSION_STATE.log_event(
                f"Detecting {image_info.target} with star subtraction",
                "info"
            )

            analysis = AnalysisService(session)
            try:
                association = analysis.auto_associate(session, capture, wcs, use_star_subtraction=True)

                if association:
                    SESSION_STATE.log_event(
                        f"Associated {image_info.target} at RA {association.ra_deg:.5f}°, "
                        f"Dec {association.dec_deg:.5f}° (residual {association.residual_arcsec:.2f}\", "
                        f"SNR {association.snr:.1f}, {association.stars_subtracted} stars subtracted)",
                        "good"
                    )
                    logger.info(
                        f"Successfully associated {image_info.target}: "
                        f"RA={association.ra_deg:.5f}, Dec={association.dec_deg:.5f}, "
                        f"residual={association.residual_arcsec:.2f}\""
                    )
                else:
                    SESSION_STATE.log_event(
                        f"Failed to associate {image_info.target} - no match found",
                        "warn"
                    )
                    logger.warning(f"Auto-association failed for {image_info.target}")

            except Exception as e:
                logger.error(f"Association error for {image_info.target}: {e}", exc_info=True)
                SESSION_STATE.log_event(
                    f"Association error for {image_info.target}: {e}",
                    "error"
                )

    def watch_for_sequence(
        self,
        expected_targets: list[str],
        timeout_seconds: float = 600.0,
        poll_interval: float = 2.0
    ) -> dict[str, list[ImageFileInfo]]:
        """
        Watch for images from a list of targets during a sequence.

        Returns a dict mapping target names to lists of image files.
        """
        start_time = time.time()
        images_by_target: dict[str, list[ImageFileInfo]] = {target: [] for target in expected_targets}

        logger.info(f"Watching for images from targets: {expected_targets}")

        while time.time() - start_time < timeout_seconds:
            new_images = self.scan_for_new_images()

            for image in new_images:
                if image.target in expected_targets:
                    images_by_target[image.target].append(image)
                    SESSION_STATE.log_event(
                        f"Received image for {image.target}: {image.path.name}",
                        "good"
                    )

            # Check if we have images for all targets
            if all(len(images) > 0 for images in images_by_target.values()):
                logger.info("Received images for all expected targets")
                break

            time.sleep(poll_interval)

        if time.time() - start_time >= timeout_seconds:
            logger.warning(f"Timeout waiting for images. Received: {sum(len(v) for v in images_by_target.values())}")

        return images_by_target


def parse_nina_filename(filename: str) -> dict[str, Any] | None:
    """
    Parse a NINA image filename and return metadata.

    Returns None if the filename doesn't match the expected pattern.
    """
    match = NINA_FILENAME_PATTERN.match(filename)
    if not match:
        return None

    return {
        "target": match.group("target"),
        "datetime": match.group("datetime"),
        "filter": match.group("filter"),
        "exposure_seconds": float(match.group("exposure")),
        "frame_number": int(match.group("frame")),
    }


__all__ = [
    "ImageMonitor",
    "ImageFileInfo",
    "parse_nina_filename",
    "NINA_FILENAME_PATTERN",
]
