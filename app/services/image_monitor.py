"""Monitor NINA image directory for new FITS files and queue them for processing."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.services.session import SESSION_STATE

logger = logging.getLogger(__name__)

# NINA filename template: $$DATEMINUS12$$\$$TARGETNAME$$\$$IMAGETYPE$$\$$TARGETNAME$$_$$DATETIME$$_$$FILTER$$_$$EXPOSURETIME$$s_$$FRAMENR$$
# Example: 20251207\A11wdXf\LIGHT\A11wdXf_2025-12-07_23-45-12_L_60.0s_001.fits

NINA_FILENAME_PATTERN = re.compile(
    r"^(?P<target>[^_]+)_"  # Target name
    r"(?P<datetime>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})_"  # DateTime
    r"(?P<filter>[^_]+)_"  # Filter
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


class ImageMonitor:
    """Monitor the NINA images directory for new FITS files."""

    def __init__(self, images_path: str | Path | None = None):
        self.images_path = Path(images_path or settings.nina_images_path or "/data/fits")
        self.seen_files: set[Path] = set()
        self.last_scan_time = time.time()

    def scan_for_new_images(self) -> list[ImageFileInfo]:
        """Scan the images directory for new FITS files since last scan."""
        if not self.images_path.exists():
            logger.warning(f"Images path does not exist: {self.images_path}")
            return []

        new_images: list[ImageFileInfo] = []

        # Scan for FITS files recursively
        for fits_file in self.images_path.rglob("*.fits"):
            if fits_file in self.seen_files:
                continue

            # Check if file was modified after last scan
            try:
                mtime = fits_file.stat().st_mtime
                if mtime < self.last_scan_time:
                    continue
            except OSError:
                continue

            # Parse filename
            image_info = self._parse_filename(fits_file)
            if image_info:
                new_images.append(image_info)
                self.seen_files.add(fits_file)
                logger.info(
                    f"Detected new image: {image_info.target} "
                    f"{image_info.filter} {image_info.exposure_seconds}s "
                    f"frame #{image_info.frame_number}"
                )

        self.last_scan_time = time.time()
        return new_images

    def _parse_filename(self, path: Path) -> ImageFileInfo | None:
        """Parse NINA filename to extract metadata."""
        # Extract image type from parent directory name
        image_type = "LIGHT"
        if path.parent.name in ("LIGHT", "DARK", "BIAS", "FLAT"):
            image_type = path.parent.name

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
                target=target,
                datetime_str="",
                filter="",
                exposure_seconds=0.0,
                frame_number=0,
                image_type=image_type
            )

        return ImageFileInfo(
            path=path,
            target=match.group("target"),
            datetime_str=match.group("datetime"),
            filter=match.group("filter"),
            exposure_seconds=float(match.group("exposure")),
            frame_number=int(match.group("frame")),
            image_type=image_type
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
