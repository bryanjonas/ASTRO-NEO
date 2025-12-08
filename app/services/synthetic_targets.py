"""Synthetic target seeder for daylight/offline testing."""

from __future__ import annotations

import argparse
import logging
import random
import time
from datetime import datetime, timedelta, timezone

import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time
from sqlmodel import delete, select

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.core.site_config import load_site_config
from app.db.session import get_session, init_db
from app.models import NeoCandidate, NeoEphemeris, NeoObservability

setup_logging(service_name="synthetic-targets")
logger = logging.getLogger(__name__)


class SyntheticTargetService:
    """Create synthetic NEOCP targets for testing slews/exposures."""

    def __init__(
        self,
        count: int | None = None,
        min_alt: float | None = None,
        max_alt: float | None = None,
        interval_minutes: int | None = None,
        prefix: str | None = None,
    ) -> None:
        self.count = count or settings.synthetic_target_count
        self.min_alt = min_alt or settings.synthetic_target_min_altitude_deg
        self.max_alt = max_alt or settings.synthetic_target_max_altitude_deg
        self.interval_seconds = max(60, (interval_minutes or settings.synthetic_target_interval_minutes) * 60)
        self.prefix = prefix or settings.synthetic_target_prefix
        self.location = self._load_location()

    def _load_location(self) -> EarthLocation:
        site = load_site_config()
        return EarthLocation(
            lat=site.latitude * u.deg,
            lon=site.longitude * u.deg,
            height=site.altitude_m * u.m,
        )

    def run_forever(self) -> None:
        logger.info(
            "Synthetic target service started (count=%s, alt=%s-%s°, interval=%ss)",
            self.count,
            self.min_alt,
            self.max_alt,
            self.interval_seconds,
        )
        try:
            while True:
                self.seed_targets()
                time.sleep(self.interval_seconds)
        except KeyboardInterrupt:
            logger.info("Synthetic target service stopping")

    def seed_targets(self) -> None:
        """Regenerate synthetic candidates + observability windows."""
        init_db()
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        with get_session() as session:
            pattern = f"{self.prefix}%"
            session.exec(delete(NeoObservability).where(NeoObservability.trksub.like(pattern)))
            session.exec(delete(NeoEphemeris).where(NeoEphemeris.trksub.like(pattern)))
            session.exec(delete(NeoCandidate).where(NeoCandidate.trksub.like(pattern)))
            session.commit()

            targets = []
            for idx in range(1, self.count + 1):
                alt = random.uniform(self.min_alt, self.max_alt)
                az = random.uniform(0, 360)
                coord = self._altaz_to_icrs(alt, az, now)
                trksub = f"{self.prefix}-{idx:02d}"
                magnitude = round(random.uniform(16.0, 19.5), 1)
                score = random.randint(70, 99)
                candidate = NeoCandidate(
                    id=trksub,
                    trksub=trksub,
                    score=score,
                    observations=5,
                    observed_ut=now.isoformat(),
                    ra_deg=coord.ra.deg,
                    dec_deg=coord.dec.deg,
                    vmag=magnitude,
                    status="Synthetic",
                    status_ut=now.isoformat(),
                    raw_entry="Synthetic target for testing",
                )
                session.add(candidate)
                session.commit()
                session.refresh(candidate)

                night_start = now
                night_end = now + timedelta(hours=6)
                window_end = now + timedelta(hours=2)
                observability = NeoObservability(
                    candidate_id=candidate.id,
                    trksub=candidate.trksub,
                    night_key=night_start.date(),
                    night_start=night_start.replace(tzinfo=None),
                    night_end=night_end.replace(tzinfo=None),
                    window_start=night_start.replace(tzinfo=None),
                    window_end=window_end.replace(tzinfo=None),
                    duration_minutes=120.0,
                    max_altitude_deg=alt,
                    min_moon_separation_deg=140.0,
                    max_sun_altitude_deg=-18.0,
                    score=float(score),
                    is_observable=True,
                    computed_at=now.replace(tzinfo=None),
                )
                session.add(observability)
                session.commit()
                session.refresh(observability)

                eph = NeoEphemeris(
                    candidate_id=candidate.id,
                    trksub=candidate.trksub,
                    epoch=now.replace(tzinfo=None),
                    ra_deg=candidate.ra_deg or 0.0,
                    dec_deg=candidate.dec_deg or 0.0,
                    magnitude=magnitude,
                )
                session.add(eph)
                session.commit()
                targets.append((candidate.trksub, alt, az))

        logger.info("Synthetic targets seeded: %s", ", ".join(f"{t} (alt={a:.1f} az={z:.1f})" for t, a, z in targets))

    def _altaz_to_icrs(self, alt_deg: float, az_deg: float, when: datetime) -> SkyCoord:
        altaz_frame = AltAz(
            alt=alt_deg * u.deg,
            az=az_deg * u.deg,
            obstime=Time(when),
            location=self.location,
        )
        coord = SkyCoord(alt=alt_deg * u.deg, az=az_deg * u.deg, frame=altaz_frame)
        return coord.transform_to("icrs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed synthetic targets for testing.")
    parser.add_argument("--count", type=int, help="Number of synthetic targets to create.")
    parser.add_argument("--min-alt", type=float, help="Minimum altitude in degrees.")
    parser.add_argument("--max-alt", type=float, help="Maximum altitude in degrees.")
    parser.add_argument("--interval", type=int, help="Refresh interval in minutes.")
    parser.add_argument("--prefix", type=str, help="Prefix for synthetic trksubs.")
    parser.add_argument("--oneshot", action="store_true", help="Run a single refresh instead of looping.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = SyntheticTargetService(
        count=args.count,
        min_alt=args.min_alt,
        max_alt=args.max_alt,
        interval_minutes=args.interval,
        prefix=args.prefix,
    )
    if args.oneshot:
        service.seed_targets()
        return 0
    service.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
