"""Background worker that refreshes observability scores on a cadence."""

from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass

from app.core.config import settings
from app.db.session import get_session
from app.services.observability import ObservabilityService

logger = logging.getLogger(__name__)


@dataclass
class EngineStats:
    total_candidates: int
    observable_count: int


class ObservabilityEngine:
    """Periodically recompute observability windows for all candidates."""

    def __init__(self, interval_minutes: int | None = None) -> None:
        refresh_minutes = interval_minutes or settings.observability_refresh_minutes
        self.interval_seconds = max(60, refresh_minutes * 60)

    def run_forever(self) -> None:
        logger.info("Starting observability engine (interval=%ss)", self.interval_seconds)
        try:
            while True:
                cycle_start = time.perf_counter()
                try:
                    stats = self.run_cycle()
                    logger.info(
                        "Observability refresh complete (total=%s, observable=%s)",
                        stats.total_candidates,
                        stats.observable_count,
                    )
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Observability refresh failed")
                elapsed = time.perf_counter() - cycle_start
                sleep_for = max(0.0, self.interval_seconds - elapsed)
                time.sleep(sleep_for)
        except KeyboardInterrupt:  # pragma: no cover - manual shutdown
            logger.info("Shutting down observability engine")

    def run_cycle(self) -> EngineStats:
        with get_session() as session:
            service = ObservabilityService(session=session)
            results = service.refresh()
            total = len(results)
            observable = sum(1 for result in results if result.is_observable)
            return EngineStats(total_candidates=total, observable_count=observable)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the observability engine loop.")
    parser.add_argument(
        "--interval",
        type=int,
        help="Refresh interval in minutes (defaults to settings).",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Run a single refresh cycle instead of looping.",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    args = parse_args()
    engine = ObservabilityEngine(interval_minutes=args.interval)
    if args.oneshot:
        stats = engine.run_cycle()
        logger.info(
            "One-shot refresh complete (total=%s, observable=%s)",
            stats.total_candidates,
            stats.observable_count,
        )
        return 0
    engine.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
