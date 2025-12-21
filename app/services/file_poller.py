"""
Simple file polling utility for synchronous FITS file detection.

This replaces the complex async image-monitor service with a simple
synchronous polling function that waits for a file to appear.
"""

import logging
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def poll_for_fits_file(
    target_name: str,
    fits_directory: Path | str = "/data/fits",
    timeout: float = 60.0,
    poll_interval: float = 0.1,
) -> Optional[Path]:
    """
    Poll for a FITS file matching the target name.

    NINA filename pattern: {TARGET}_{DATETIME}__{EXPOSURE}s_{FRAME}.fits
    Example: ZTF109i_2025-12-20_20-28-04__102.00s_0000.fits

    Args:
        target_name: The target name to match in the filename
        fits_directory: Directory to monitor for FITS files
        timeout: Maximum time to wait in seconds
        poll_interval: How often to check in seconds (start value for exponential backoff)

    Returns:
        Path to the FITS file if found, None if timeout
    """
    fits_dir = Path(fits_directory)
    if not fits_dir.exists():
        logger.error(f"FITS directory does not exist: {fits_dir}")
        return None

    deadline = time.time() + timeout
    current_interval = poll_interval

    logger.info(f"Polling for FITS file with target name '{target_name}' in {fits_dir}")

    while time.time() < deadline:
        # Find all FITS files that match the target name
        # Use glob pattern to find files
        matching_files = list(fits_dir.glob(f"{target_name}_*.fits"))

        if matching_files:
            # Sort by modification time and return the most recent
            matching_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            newest_file = matching_files[0]
            logger.info(f"Found FITS file: {newest_file}")
            return newest_file

        # Exponential backoff with max interval of 3.2 seconds
        time.sleep(current_interval)
        current_interval = min(current_interval * 2.0, 3.2)

    logger.error(f"Timeout waiting for FITS file with target '{target_name}' after {timeout}s")
    return None


def wait_for_file_size_stable(
    file_path: Path,
    stable_duration: float = 2.0,
    check_interval: float = 0.5,
    timeout: float = 30.0,
) -> bool:
    """
    Wait for a file's size to stabilize (indicating write is complete).

    Args:
        file_path: Path to the file to monitor
        stable_duration: How long the size must remain constant (seconds)
        check_interval: How often to check the size (seconds)
        timeout: Maximum time to wait (seconds)

    Returns:
        True if file size stabilized, False if timeout
    """
    if not file_path.exists():
        logger.error(f"File does not exist: {file_path}")
        return False

    deadline = time.time() + timeout
    last_size = -1
    stable_since = 0.0

    while time.time() < deadline:
        try:
            current_size = file_path.stat().st_size

            if current_size == last_size:
                # Size hasn't changed
                if stable_since == 0.0:
                    stable_since = time.time()
                elif time.time() - stable_since >= stable_duration:
                    logger.info(f"File size stable at {current_size} bytes: {file_path}")
                    return True
            else:
                # Size changed, reset stability timer
                last_size = current_size
                stable_since = 0.0

            time.sleep(check_interval)
        except Exception as e:
            logger.warning(f"Error checking file size: {e}")
            time.sleep(check_interval)

    logger.error(f"Timeout waiting for file size to stabilize: {file_path}")
    return False


__all__ = ["poll_for_fits_file", "wait_for_file_size_stable"]
