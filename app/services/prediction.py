"""Utilities for predicting NEO positions from cached MPC ephemerides."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable, Sequence

from sqlmodel import Session

from app.core.config import settings
from app.core.site_config import load_site_config
from app.models import NeoCandidate, NeoEphemeris
from app.services.ephemeris import MpcEphemerisClient


class EphemerisPredictionService:
    """Predict RA/Dec for a candidate by interpolating cached ephemerides."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.site_config = load_site_config()
        self.client = MpcEphemerisClient(session, self.site_config)
        self.sample_minutes = max(1, settings.observability_sample_minutes)
        self.margin_minutes = self.sample_minutes * 3

    def predict(self, candidate_id: str | None, when: datetime) -> tuple[float, float] | None:
        if not candidate_id:
            return None
        candidate = self.session.get(NeoCandidate, candidate_id)
        if not candidate or candidate.ra_deg is None or candidate.dec_deg is None:
            return None

        start = (when - timedelta(minutes=self.margin_minutes)).replace(second=0, microsecond=0)
        end = (when + timedelta(minutes=self.margin_minutes)).replace(second=0, microsecond=0)
        expected_count = int((end - start).total_seconds() / 60 / self.sample_minutes) + 1
        rows = self.client.get_or_fetch(
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
        delta = ((end - start + 180.0) % 360.0) - 180.0
        return (start + delta * fraction) % 360.0


__all__ = ["EphemerisPredictionService"]
