"""Test script to verify NINA bridge correctly extracts and returns file paths from camera captures."""

import logging
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.nina_client import NinaBridgeService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


def test_file_path_extraction():
    """Test that camera capture returns file path correctly."""

    bridge = NinaBridgeService()

    logger.info("Testing camera capture with file path extraction...")

    try:
        # Attempt a quick test exposure
        result = bridge.start_exposure(
            filter_name="L",
            binning=2,
            exposure_seconds=1.0,
            target="FILE-PATH-TEST",
        )

        logger.info("Capture result type: %s", type(result))
        logger.info("Capture result keys: %s", result.keys() if isinstance(result, dict) else "N/A")

        # Check if file path is present
        file_path = result.get("file")
        platesolve = result.get("platesolve")
        nina_response = result.get("nina_response")

        logger.info("=" * 60)
        logger.info("FILE PATH EXTRACTION TEST RESULTS")
        logger.info("=" * 60)
        logger.info("File path returned: %s", file_path or "NONE")
        logger.info("Platesolve present: %s", bool(platesolve))
        logger.info("NINA response present: %s", bool(nina_response))

        if file_path:
            logger.info("✓ SUCCESS: File path correctly extracted from NINA response")
            logger.info("  File location: %s", file_path)

            # Verify file path looks valid
            if file_path.startswith("/data/") or file_path.startswith("\\data\\"):
                logger.info("✓ File path has expected prefix (/data/)")
            else:
                logger.warning("⚠ File path does not start with /data/: %s", file_path)

        else:
            logger.error("✗ FAILED: No file path returned in result")
            logger.error("  This means file monitoring will not work!")
            logger.error("  Raw result: %s", result)
            return False

        # Additional diagnostic info
        if nina_response:
            logger.info("\nNINA Response diagnostic info:")
            saved_file_path = nina_response.get("SavedFilePath")
            file_path_alt = nina_response.get("FilePath")
            file_alt = nina_response.get("File")

            logger.info("  SavedFilePath: %s", saved_file_path or "not present")
            logger.info("  FilePath: %s", file_path_alt or "not present")
            logger.info("  File: %s", file_alt or "not present")

        logger.info("=" * 60)
        return True

    except Exception as exc:
        logger.error("Camera capture test failed: %s", exc, exc_info=True)
        return False


if __name__ == "__main__":
    success = test_file_path_extraction()
    sys.exit(0 if success else 1)
