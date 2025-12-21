"""Utilities for predicting NEO positions using JPL Horizons ephemerides."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Sequence

from sqlmodel import Session, select

from app.core.config import settings
from app.core.site_config import load_site_config
from app.models import NeoCandidate, NeoEphemeris
from app.services.horizons_client import HorizonsClient
from app.services.ephemeris import MpcEphemerisClient

logger = logging.getLogger(__name__)


class EphemerisPredictionService:
    """Predict RA/Dec for a candidate using JPL Horizons ephemerides.

    Strategy:
    - Always use Horizons for authoritative topocentric coordinates
    - Cache Horizons ephemerides in database
    - Re-fetch if cache is stale (> horizons_cache_hours old)
    - Fallback to MPC only if Horizons fails
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.site_config = load_site_config()
        self.mpc_client = MpcEphemerisClient(session, self.site_config)
        self.horizons_client = None
        if settings.use_horizons_ephemerides:
            self.horizons_client = HorizonsClient(
                site_lat=self.site_config.latitude,
                site_lon=self.site_config.longitude,
                site_alt_m=self.site_config.altitude_m,
                timeout=settings.horizons_timeout,
            )
        self.sample_minutes = max(1, settings.horizons_step_minutes)
        self.margin_minutes = 60  # Fetch ±1 hour window for interpolation
        self.cache_hours = settings.horizons_cache_hours

    def predict(
        self,
        candidate_id: str | None,
        when: datetime,
    ) -> tuple[float, float] | None:
        """Predict RA/Dec at specified time using Horizons ephemerides.

        Args:
            candidate_id: NeoCandidate database ID
            when: Time for ephemeris prediction (UTC)

        Returns:
            Tuple of (ra_deg, dec_deg) or None if prediction fails
        """

        if not candidate_id:
            return None

        candidate = self.session.get(NeoCandidate, candidate_id)
        if not candidate or candidate.ra_deg is None or candidate.dec_deg is None:
            return None

        # Always use Horizons when enabled
        if self.horizons_client:
            try:
                return self._predict_from_horizons(candidate, when)
            except Exception as exc:
                logger.warning(
                    "Horizons prediction failed for %s at %s: %s",
                    candidate.trksub,
                    when.isoformat(),
                    exc,
                )
                # Fallback to MPC
                logger.info("Falling back to MPC ephemerides for %s", candidate.trksub)

        # Fallback: use MPC ephemerides
        return self._predict_from_mpc(candidate, when)

    def _predict_from_horizons(
        self, candidate: NeoCandidate, when: datetime
    ) -> tuple[float, float] | None:
        """Fetch/use Horizons ephemeris for prediction."""

        # Check if we have fresh cached Horizons data
        cache_cutoff = datetime.utcnow() - timedelta(hours=self.cache_hours)
        start_window = when - timedelta(minutes=self.margin_minutes)
        end_window = when + timedelta(minutes=self.margin_minutes)

        cached_rows = self.session.exec(
            select(NeoEphemeris)
            .where(NeoEphemeris.candidate_id == candidate.id)
            .where(NeoEphemeris.source == "HORIZONS")
            .where(NeoEphemeris.epoch >= start_window)
            .where(NeoEphemeris.epoch <= end_window)
            .where(NeoEphemeris.created_at >= cache_cutoff)
            .order_by(NeoEphemeris.epoch)
        ).all()

        # If we have enough fresh cache data, use it
        if len(cached_rows) >= 5:  # Need at least 5 points for good interpolation
            logger.debug(
                "Using %d cached Horizons ephemeris points for %s",
                len(cached_rows),
                candidate.trksub,
            )
            return self._interpolate(cached_rows, when)

        # Otherwise, fetch fresh Horizons data
        logger.info("Fetching fresh Horizons ephemeris for %s", candidate.trksub)

        start_fetch = when - timedelta(minutes=self.margin_minutes)
        end_fetch = when + timedelta(minutes=self.margin_minutes)

        rows_data = self.horizons_client.fetch_ephemeris(
            target_designation=candidate.trksub,
            start_time=start_fetch,
            stop_time=end_fetch,
            step_minutes=self.sample_minutes,
        )

        if not rows_data:
            logger.warning("Horizons returned no ephemeris data for %s", candidate.trksub)
            return None

        # Cache Horizons data in database
        self._cache_horizons_ephemerides(candidate.id, candidate.trksub, rows_data)

        # Convert to NeoEphemeris objects for interpolation
        ephemeris_rows = []
        for row_data in rows_data:
            eph = NeoEphemeris(
                candidate_id=candidate.id,
                trksub=candidate.trksub,
                epoch=row_data["epoch"],
                ra_deg=row_data["ra_deg"],
                dec_deg=row_data["dec_deg"],
                ra_rate_arcsec_min=row_data.get("ra_rate_arcsec_min"),
                dec_rate_arcsec_min=row_data.get("dec_rate_arcsec_min"),
                azimuth_deg=row_data.get("azimuth_deg"),
                elevation_deg=row_data.get("elevation_deg"),
                airmass=row_data.get("airmass"),
                v_mag_predicted=row_data.get("v_mag"),
                solar_elongation_deg=row_data.get("solar_elongation_deg"),
                lunar_elongation_deg=row_data.get("lunar_elongation_deg"),
                uncertainty_3sigma_arcsec=row_data.get("uncertainty_3sigma_arcsec"),
                source="HORIZONS",
            )
            ephemeris_rows.append(eph)

        return self._interpolate(ephemeris_rows, when)

    def _cache_horizons_ephemerides(
        self, candidate_id: str, trksub: str, rows_data: list[dict]
    ) -> None:
        """Store Horizons ephemeris data in database."""

        for row_data in rows_data:
            # Check if this epoch already exists
            existing = self.session.exec(
                select(NeoEphemeris)
                .where(NeoEphemeris.candidate_id == candidate_id)
                .where(NeoEphemeris.epoch == row_data["epoch"])
            ).first()

            if existing:
                # Update existing record
                existing.ra_deg = row_data["ra_deg"]
                existing.dec_deg = row_data["dec_deg"]
                existing.ra_rate_arcsec_min = row_data.get("ra_rate_arcsec_min")
                existing.dec_rate_arcsec_min = row_data.get("dec_rate_arcsec_min")
                existing.azimuth_deg = row_data.get("azimuth_deg")
                existing.elevation_deg = row_data.get("elevation_deg")
                existing.airmass = row_data.get("airmass")
                existing.v_mag_predicted = row_data.get("v_mag")
                existing.solar_elongation_deg = row_data.get("solar_elongation_deg")
                existing.lunar_elongation_deg = row_data.get("lunar_elongation_deg")
                existing.uncertainty_3sigma_arcsec = row_data.get("uncertainty_3sigma_arcsec")
                existing.source = "HORIZONS"
                existing.created_at = datetime.utcnow()  # Update timestamp
                self.session.add(existing)
            else:
                # Create new record
                eph = NeoEphemeris(
                    candidate_id=candidate_id,
                    trksub=trksub,
                    epoch=row_data["epoch"],
                    ra_deg=row_data["ra_deg"],
                    dec_deg=row_data["dec_deg"],
                    ra_rate_arcsec_min=row_data.get("ra_rate_arcsec_min"),
                    dec_rate_arcsec_min=row_data.get("dec_rate_arcsec_min"),
                    azimuth_deg=row_data.get("azimuth_deg"),
                    elevation_deg=row_data.get("elevation_deg"),
                    airmass=row_data.get("airmass"),
                    v_mag_predicted=row_data.get("v_mag"),
                    solar_elongation_deg=row_data.get("solar_elongation_deg"),
                    lunar_elongation_deg=row_data.get("lunar_elongation_deg"),
                    uncertainty_3sigma_arcsec=row_data.get("uncertainty_3sigma_arcsec"),
                    source="HORIZONS",
                )
                self.session.add(eph)

        self.session.commit()

    def _predict_from_mpc(
        self, candidate: NeoCandidate, when: datetime
    ) -> tuple[float, float] | None:
        """Fallback: use MPC ephemerides."""

        start = (when - timedelta(minutes=self.margin_minutes)).replace(second=0, microsecond=0)
        end = (when + timedelta(minutes=self.margin_minutes)).replace(second=0, microsecond=0)
        expected_count = int((end - start).total_seconds() / 60 / self.sample_minutes) + 1

        rows = self.mpc_client.get_or_fetch(
            candidate=candidate,
            start_utc=start,
            end_utc=end,
            expected_count=expected_count,
            sample_minutes=self.sample_minutes,
        )

        if not rows:
            return None

        rows = sorted(rows, key=lambda row: row.epoch)
        return self._interpolate(rows, when)

    def _interpolate(
        self, rows: Sequence[NeoEphemeris], when: datetime
    ) -> tuple[float, float] | None:
        """Interpolate RA/Dec from ephemeris points."""
        before: NeoEphemeris | None = None
        after: NeoEphemeris | None = None
        for row in rows:
            if row.epoch <= when:
                before = row
            if row.epoch >= when and after is None:
                after = row
                if row.epoch == when:
                    before = row
                break

        if before is None and after is None:
            return None
        if before is None:
            return (after.ra_deg or 0.0, after.dec_deg or 0.0)
        if after is None:
            return (before.ra_deg or 0.0, before.dec_deg or 0.0)
        if before.ra_deg is None or before.dec_deg is None:
            return None
        if after.ra_deg is None or after.dec_deg is None:
            return (before.ra_deg, before.dec_deg)

        if before.epoch == after.epoch:
            return (before.ra_deg, before.dec_deg)

        fraction = (when - before.epoch).total_seconds() / (after.epoch - before.epoch).total_seconds()
        ra = self._interpolate_angle(before.ra_deg, after.ra_deg, fraction)
        dec = before.dec_deg + (after.dec_deg - before.dec_deg) * fraction
        return (ra, dec)

    def _interpolate_angle(self, start: float, end: float, fraction: float) -> float:
        """Interpolate angle accounting for wraparound at 360°."""
        delta = ((end - start + 180.0) % 360.0) - 180.0
        return (start + delta * fraction) % 360.0


__all__ = ["EphemerisPredictionService"]
