"""Background worker that monitors for new FITS images and triggers processing."""

from __future__ import annotations

import argparse
import logging
import time

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.services.image_monitor import ImageMonitor

setup_logging(service_name="image-monitor")
logger = logging.getLogger(__name__)


class ImageMonitorService:
    """Periodically scan for new FITS images and trigger processing."""

    def __init__(self, poll_interval_seconds: float | None = None) -> None:
        self.monitor = ImageMonitor(settings.nina_images_path)
        self.poll_interval = poll_interval_seconds or 2.0  # Default: check every 2 seconds

    def run_forever(self) -> None:
        logger.info(
            "Starting image monitor service (poll_interval=%.1fs, path=%s)",
            self.poll_interval,
            self.monitor.images_path
        )
        try:
            while True:
                cycle_start = time.perf_counter()
                try:
                    new_images = self.monitor.scan_for_new_images()
                    if new_images:
                        logger.info("Detected %d new images", len(new_images))
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Image scan cycle failed")

                elapsed = time.perf_counter() - cycle_start
                sleep_for = max(0.0, self.poll_interval - elapsed)
                time.sleep(sleep_for)
        except KeyboardInterrupt:  # pragma: no cover - manual shutdown
            logger.info("Shutting down image monitor service")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the image monitor service loop.")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Poll interval in seconds (default: 2.0).",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Run a single scan cycle instead of looping.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = ImageMonitorService(poll_interval_seconds=args.poll_interval)

    if args.oneshot:
        new_images = service.monitor.scan_for_new_images()
        logger.info("One-shot scan complete (detected %d new images)", len(new_images))
        return 0

    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
