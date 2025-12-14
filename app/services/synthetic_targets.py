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
        full_day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        full_day_end = full_day_start + timedelta(days=1)
        window_start_naive = full_day_start.replace(tzinfo=None)
        window_end_naive = full_day_end.replace(tzinfo=None)
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

                # Simulate last observation time (recent for arc extension testing)
                hours_ago = random.uniform(1, 48)  # 1-48 hours ago
                last_obs = now - timedelta(hours=hours_ago)

                candidate = NeoCandidate(
                    id=trksub,
                    trksub=trksub,
                    score=score,
                    observations=5,
                    observed_ut=now.isoformat(),
                    last_obs_utc=last_obs.replace(tzinfo=None),
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

                # Calculate composite score for dynamic prioritization testing
                # Simulate realistic scoring based on multiple factors
                composite_score = self._calculate_synthetic_composite_score(
                    mpc_score=score,
                    altitude=alt,
                    hours_since_obs=hours_ago,
                )

                observability = NeoObservability(
                    candidate_id=candidate.id,
                    trksub=candidate.trksub,
                    night_key=full_day_start.date(),
                    night_start=night_start.replace(tzinfo=None),
                    night_end=night_end.replace(tzinfo=None),
                    window_start=window_start_naive,
                    window_end=window_end_naive,
                    duration_minutes=120.0,
                    max_altitude_deg=alt,
                    peak_altitude_deg=alt,  # For testing, peak = current
                    min_moon_separation_deg=140.0,
                    max_sun_altitude_deg=-18.0,
                    score=float(score),
                    composite_score=composite_score,
                    is_observable=True,
                    computed_at=now.replace(tzinfo=None),
                )
                session.add(observability)
                session.commit()
                session.refresh(observability)

                # Simulate realistic motion rates for fast-mover testing
                # Mix of slow (< 10 "/min), moderate (10-30), fast (30-60), very fast (> 60)
                motion_category = random.choices(
                    ["slow", "moderate", "fast", "very_fast"],
                    weights=[0.50, 0.30, 0.15, 0.05],  # Most are slow movers
                    k=1
                )[0]

                if motion_category == "slow":
                    total_motion = random.uniform(1.0, 10.0)
                elif motion_category == "moderate":
                    total_motion = random.uniform(10.0, 30.0)
                elif motion_category == "fast":
                    total_motion = random.uniform(30.0, 60.0)
                else:  # very_fast
                    total_motion = random.uniform(60.0, 120.0)

                # Split into RA and Dec components
                motion_angle = random.uniform(0, 360)
                ra_rate = total_motion * random.uniform(0.5, 1.0)  # arcsec/min
                dec_rate = (total_motion**2 - ra_rate**2)**0.5 if total_motion > ra_rate else 0.0

                eph = NeoEphemeris(
                    candidate_id=candidate.id,
                    trksub=candidate.trksub,
                    epoch=now.replace(tzinfo=None),
                    ra_deg=candidate.ra_deg or 0.0,
                    dec_deg=candidate.dec_deg or 0.0,
                    magnitude=magnitude,
                    # Horizons-specific fields for testing
                    ra_rate_arcsec_min=ra_rate,
                    dec_rate_arcsec_min=dec_rate,
                    azimuth_deg=az,
                    elevation_deg=alt,
                    airmass=self._calculate_airmass(alt),
                    solar_elongation_deg=random.uniform(90, 180),
                    lunar_elongation_deg=random.uniform(30, 180),
                    v_mag_predicted=magnitude,
                    uncertainty_3sigma_arcsec=random.uniform(5, 60),
                    source="HORIZONS",  # Mark as Horizons for testing
                )
                session.add(eph)
                session.commit()
                targets.append((candidate.trksub, alt, az, total_motion))

        logger.info(
            "Synthetic targets seeded: %s",
            ", ".join(f"{t[0]} (alt={t[1]:.1f}° az={t[2]:.1f}° motion={t[3]:.1f}\"/min)" for t in targets)
        )

    def _calculate_airmass(self, alt_deg: float) -> float:
        """Calculate airmass from altitude using plane-parallel approximation."""
        import math
        if alt_deg <= 0:
            return 99.0
        zenith_angle_rad = math.radians(90.0 - alt_deg)
        return 1.0 / math.cos(zenith_angle_rad) if alt_deg > 0 else 99.0

    def _calculate_synthetic_composite_score(
        self, mpc_score: int, altitude: float, hours_since_obs: float
    ) -> float:
        """Calculate simplified composite score for synthetic targets.

        Mimics the real scoring algorithm with reasonable weights.
        """
        # MPC priority (30%)
        s_mpc = float(mpc_score)

        # Altitude score (25%)
        if altitude > 60:
            s_alt = 100.0
        elif altitude > 45:
            s_alt = 80.0 + (altitude - 45) * (20.0 / 15.0)
        elif altitude > 30:
            s_alt = 50.0 + (altitude - 30) * (30.0 / 15.0)
        else:
            s_alt = max(0.0, altitude * (50.0 / 30.0))

        # Time to set (15%) - assume 2 hours remaining (moderate)
        s_time = 70.0

        # Motion rate (10%) - assume moderate slow movers (good score)
        s_motion = 85.0

        # Uncertainty (10%) - assume moderate uncertainty
        s_uncertainty = 70.0

        # Arc extension (10%) - based on hours since last obs
        if hours_since_obs < 6:
            s_arc = 100.0
        elif hours_since_obs < 24:
            s_arc = 70.0 + (24 - hours_since_obs) * (30.0 / 18.0)
        elif hours_since_obs < 72:
            s_arc = 40.0 + (72 - hours_since_obs) * (30.0 / 48.0)
        else:
            s_arc = max(0.0, 40.0 - (hours_since_obs - 72) * (40.0 / 168.0))

        # Weighted composite (using default weights)
        composite = (
            0.30 * s_mpc
            + 0.25 * s_alt
            + 0.15 * s_time
            + 0.10 * s_motion
            + 0.10 * s_uncertainty
            + 0.10 * s_arc
        )

        return min(100.0, max(0.0, composite))

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
