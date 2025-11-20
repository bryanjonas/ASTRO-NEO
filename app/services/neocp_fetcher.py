"""Long-running service that polls the MPC NEOCP and observation API."""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Iterable, Sequence

import httpx
from prometheus_client import Counter, Gauge, Histogram, start_http_server
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session
from app.models import NeoObservationPayload, NeoCPSnapshot
from app.services.neocp import (
    CandidatePayload,
    diff_candidate_payloads,
    fetch_neocp_feed,
    parse_candidates,
    sync_candidates,
)

logger = logging.getLogger(__name__)

_METRICS_SERVER_STARTED = False

FETCH_CYCLE_SECONDS = Histogram(
    "neocp_fetcher_cycle_seconds",
    "End-to-end runtime for each neocp-fetcher cycle.",
)
FEED_FETCH_LATENCY_SECONDS = Histogram(
    "neocp_fetcher_feed_fetch_seconds",
    "Latency for downloading the MPC NEOCP feed (text or HTML).",
)
OBS_FETCH_LATENCY_SECONDS = Histogram(
    "neocp_fetcher_observation_fetch_seconds",
    "Latency for retrieving each MPC observation payload.",
)
FETCH_CYCLE_SUCCESS = Counter(
    "neocp_fetcher_cycles_success_total",
    "Number of successful neocp-fetcher cycles.",
)
FETCH_CYCLE_FAILURE = Counter(
    "neocp_fetcher_cycles_failure_total",
    "Number of failed neocp-fetcher cycles.",
)
OBS_REQUESTS_TOTAL = Counter(
    "neocp_fetcher_observation_requests_total",
    "Total MPC observation payload requests issued.",
)
OBS_PAYLOADS_SAVED = Counter(
    "neocp_fetcher_observation_payloads_saved_total",
    "Observation payloads persisted after dedupe.",
)
OBS_REQUEST_FAILURES = Counter(
    "neocp_fetcher_observation_request_failures_total",
    "Observation payload requests that failed even after retries.",
)
RATE_LIMIT_HITS = Counter(
    "neocp_fetcher_rate_limit_hits_total",
    "Count of HTTP 429 responses returned by MPC.",
)
CURRENT_CANDIDATES = Gauge(
    "neocp_fetcher_candidates_current",
    "Number of parsed NEOCP candidates from the most recent poll.",
)


@dataclass
class ObservationSyncStats:
    requested: int = 0
    stored: int = 0
    failures: int = 0


@dataclass
class FetchCycleStats:
    cycle_started: datetime
    total_candidates: int
    new_trksubs: list[str]
    updated_trksubs: list[str]
    snapshot_saved: bool
    observation_stats: ObservationSyncStats


class NeoCPFetcherService:
    """Background worker that keeps the local database aligned with MPC."""

    def __init__(
        self,
        interval_seconds: int | None = None,
        use_local_sample: bool | None = None,
        observation_formats: Sequence[str] | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds or settings.neocp_poll_interval_seconds
        self.use_local_sample = (
            settings.neocp_use_local_sample if use_local_sample is None else use_local_sample
        )
        self.output_formats: tuple[str, ...] = tuple(
            observation_formats or settings.neocp_observation_formats
        )
        self.api_pause_seconds = max(0.0, settings.neocp_api_pause_seconds)
        self.api_max_retries = max(1, settings.neocp_api_max_retries)
        self._metrics_enabled = settings.neocp_metrics_enabled
        self._start_metrics_server()
        self._client = httpx.Client(timeout=settings.neocp_fetch_timeout)

    def run_forever(self) -> None:
        logger.info(
            "Starting neocp-fetcher worker (interval=%ss, formats=%s)",
            self.interval_seconds,
            ", ".join(self.output_formats),
        )
        try:
            while True:
                cycle_wall_start = time.perf_counter()
                try:
                    stats = self.run_cycle()
                    duration = time.perf_counter() - cycle_wall_start
                    logger.info(
                        "Cycle completed (candidates=%s, new=%s, updated=%s, "
                        "obs_requests=%s, obs_new=%s)",
                        stats.total_candidates,
                        len(stats.new_trksubs),
                        len(stats.updated_trksubs),
                        stats.observation_stats.requested,
                        stats.observation_stats.stored,
                    )
                    self._record_cycle_metrics(stats, duration)
                except Exception:  # pragma: no cover - defensive logging
                    logger.exception("Failed neocp-fetcher cycle")
                    if self._metrics_enabled:
                        FETCH_CYCLE_FAILURE.inc()
                time.sleep(self.interval_seconds)
        except KeyboardInterrupt:
            logger.info("Shutting down neocp-fetcher")
        finally:
            self._client.close()

    def run_cycle(self) -> FetchCycleStats:
        """Execute a single fetch + sync cycle."""

        feed_started = time.perf_counter()
        feed, source_format, source_url = fetch_neocp_feed(use_local=self.use_local_sample)
        if self._metrics_enabled:
            FEED_FETCH_LATENCY_SECONDS.observe(time.perf_counter() - feed_started)
        payloads = parse_candidates(feed, source_format=source_format)
        cycle_start = datetime.utcnow()

        with get_session() as session:
            snapshot_saved = self._persist_snapshot(session, feed, source_url)
            diff = diff_candidate_payloads(payloads, session)
            sync_candidates(payloads, session=session)
            observation_stats = self._sync_observations(session, payloads)

        return FetchCycleStats(
            cycle_started=cycle_start,
            total_candidates=len(payloads),
            new_trksubs=diff.new,
            updated_trksubs=diff.updated,
            snapshot_saved=snapshot_saved,
            observation_stats=observation_stats,
        )

    def _persist_snapshot(self, session: Session, payload: str, source_url: str) -> bool:
        checksum = sha256(payload.encode("utf-8")).hexdigest()
        existing = session.exec(
            select(NeoCPSnapshot).where(NeoCPSnapshot.checksum == checksum)
        ).first()
        if existing:
            return False

        snapshot = NeoCPSnapshot(
            source_url=source_url,
            checksum=checksum,
            html=payload,
            fetched_at=datetime.utcnow(),
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return True

    def _sync_observations(
        self, session: Session, payloads: Iterable[CandidatePayload]
    ) -> ObservationSyncStats:
        trksubs = sorted({payload.trksub for payload in payloads})
        stats = ObservationSyncStats()

        if not trksubs or not self.output_formats:
            return stats

        for trksub in trksubs:
            stats.requested += 1
            try:
                response = self._fetch_observation_payload(trksub)
            except Exception as exc:  # pragma: no cover - network variability
                stats.failures += 1
                logger.warning("Failed to fetch observation payload for %s: %s", trksub, exc)
                if self._metrics_enabled:
                    OBS_REQUEST_FAILURES.inc()
                continue

            saved = self._persist_observation_formats(session, trksub, response)
            stats.stored += saved
            time.sleep(self.api_pause_seconds)

        return stats

    def _fetch_observation_payload(self, trksub: str) -> dict:
        attempt = 0
        while attempt < self.api_max_retries:
            attempt += 1
            start_time = time.perf_counter()
            params = {
                "trksubs": [trksub],
                "output_format": list(self.output_formats),
                "ades_version": settings.neocp_ades_version,
            }
            response = self._client.request("GET", settings.neocp_api_url, json=params)
            latency = time.perf_counter() - start_time
            if self._metrics_enabled:
                OBS_FETCH_LATENCY_SECONDS.observe(latency)
            if response.status_code == 429:
                RATE_LIMIT_HITS.inc()
                sleep_seconds = self.api_pause_seconds * attempt or 1.0
                logger.warning(
                    "MPC rate limit hit for %s (attempt %s/%s); sleeping %ss",
                    trksub,
                    attempt,
                    self.api_max_retries,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list) or not data:
                raise RuntimeError("Unexpected MPC API response format")
            return data[0]
        raise RuntimeError(f"Exceeded MPC retries for {trksub}")

    def _persist_observation_formats(
        self, session: Session, trksub: str, response: dict
    ) -> int:
        saved = 0
        fetched_at = datetime.utcnow()

        for fmt in self.output_formats:
            if fmt not in response:
                logger.debug("Format %s missing from MPC response for %s", fmt, trksub)
                continue
            payload_data = response[fmt]
            serialized = json.dumps(payload_data, sort_keys=True)
            checksum = sha256(serialized.encode("utf-8")).hexdigest()
            existing = session.exec(
                select(NeoObservationPayload).where(
                    NeoObservationPayload.trksub == trksub,
                    NeoObservationPayload.output_format == fmt,
                    NeoObservationPayload.checksum == checksum,
                )
            ).first()
            if existing:
                continue

            record = NeoObservationPayload(
                trksub=trksub,
                output_format=fmt,
                ades_version=settings.neocp_ades_version,
                payload_json=serialized,
                checksum=checksum,
                fetched_at=fetched_at,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            saved += 1

        return saved

    def _start_metrics_server(self) -> None:
        global _METRICS_SERVER_STARTED
        if not self._metrics_enabled or _METRICS_SERVER_STARTED:
            return
        start_http_server(settings.neocp_metrics_port, addr=settings.neocp_metrics_host)
        logger.info(
            "Prometheus metrics exporter listening on %s:%s",
            settings.neocp_metrics_host,
            settings.neocp_metrics_port,
        )
        _METRICS_SERVER_STARTED = True

    def _record_cycle_metrics(self, stats: FetchCycleStats, duration: float) -> None:
        if not self._metrics_enabled:
            return
        FETCH_CYCLE_SECONDS.observe(duration)
        FETCH_CYCLE_SUCCESS.inc()
        CURRENT_CANDIDATES.set(stats.total_candidates)
        OBS_REQUESTS_TOTAL.inc(stats.observation_stats.requested)
        OBS_PAYLOADS_SAVED.inc(stats.observation_stats.stored)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the neocp-fetcher worker loop.")
    parser.add_argument(
        "--interval",
        type=int,
        help="Polling interval in seconds (defaults to settings)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use the local ToConfirm snapshot instead of live MPC fetches.",
    )
    parser.add_argument(
        "--oneshot",
        action="store_true",
        help="Run a single fetch cycle instead of the continuous loop.",
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        help="Override output formats requested from MPC (e.g., ADES_DF OBS80).",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    args = parse_args()
    service = NeoCPFetcherService(
        interval_seconds=args.interval,
        use_local_sample=args.local or None,
        observation_formats=args.formats,
    )

    if args.oneshot:
        stats = service.run_cycle()
        logger.info(
            "One-shot cycle complete (candidates=%s, new=%s, updated=%s, obs_new=%s)",
            stats.total_candidates,
            len(stats.new_trksubs),
            len(stats.updated_trksubs),
            stats.observation_stats.stored,
        )
        return 0

    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
