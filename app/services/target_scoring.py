"""Dynamic target scoring for NEOCP prioritization."""

from __future__ import annotations

import logging
import math
from datetime import datetime

from app.core.config import settings
from app.models import NeoCandidate, NeoEphemeris, NeoObservability

logger = logging.getLogger(__name__)


class TargetScoringService:
    """Multi-factor scoring for NEO target prioritization.

    Combines multiple factors to produce composite score (0-100):
    - MPC priority score (30%)
    - Current altitude/airmass (25%)
    - Time remaining until object sets (15%)
    - Apparent motion rate (10% - slower is easier)
    - Positional uncertainty (10% - lower is better)
    - Arc extension value (10% - recent obs = high value)
    """

    def __init__(
        self,
        weight_mpc: float | None = None,
        weight_altitude: float | None = None,
        weight_time_to_set: float | None = None,
        weight_motion_rate: float | None = None,
        weight_uncertainty: float | None = None,
        weight_arc_extension: float | None = None,
    ):
        """Initialize with configurable weights (default from settings)."""
        self.weight_mpc = weight_mpc or settings.scoring_weight_mpc
        self.weight_altitude = weight_altitude or settings.scoring_weight_altitude
        self.weight_time_to_set = weight_time_to_set or settings.scoring_weight_time_to_set
        self.weight_motion_rate = weight_motion_rate or settings.scoring_weight_motion_rate
        self.weight_uncertainty = weight_uncertainty or settings.scoring_weight_uncertainty
        self.weight_arc_extension = weight_arc_extension or settings.scoring_weight_arc_extension

    def score_target(
        self,
        candidate: NeoCandidate,
        observability: NeoObservability,
        ephemeris: NeoEphemeris | None,
        current_time: datetime,
    ) -> float:
        """Compute composite score (0-100) for target prioritization.

        Higher scores = higher priority for imaging.

        Args:
            candidate: NEO candidate with MPC data
            observability: Observability window data
            ephemeris: Current ephemeris (optional, for rates/uncertainty)
            current_time: Current UTC time

        Returns:
            Composite score 0-100
        """

        # Component scores (each 0-100)
        s_mpc = self._score_mpc_priority(candidate)
        s_alt = self._score_altitude(observability, ephemeris)
        s_time = self._score_time_to_set(observability, current_time)
        s_motion = self._score_motion_rate(ephemeris)
        s_uncertainty = self._score_uncertainty(candidate, ephemeris)
        s_arc = self._score_arc_extension(candidate, current_time)

        # Weighted combination
        composite = (
            self.weight_mpc * s_mpc
            + self.weight_altitude * s_alt
            + self.weight_time_to_set * s_time
            + self.weight_motion_rate * s_motion
            + self.weight_uncertainty * s_uncertainty
            + self.weight_arc_extension * s_arc
        )

        logger.debug(
            "Scoring %s: MPC=%.1f Alt=%.1f Time=%.1f Motion=%.1f Uncert=%.1f Arc=%.1f => %.1f",
            candidate.trksub,
            s_mpc,
            s_alt,
            s_time,
            s_motion,
            s_uncertainty,
            s_arc,
            composite,
        )

        return min(100.0, max(0.0, composite))

    def _score_mpc_priority(self, candidate: NeoCandidate) -> float:
        """MPC score passthrough (0-100)."""
        return float(candidate.score or 50)

    def _score_altitude(
        self, observability: NeoObservability, ephemeris: NeoEphemeris | None
    ) -> float:
        """Altitude/airmass score (higher altitude = better).

        Score breakdown:
        - Alt > 60°: 100 (excellent)
        - Alt 45-60°: 80-100 (good)
        - Alt 30-45°: 50-80 (acceptable)
        - Alt < 30°: 0-50 (poor airmass)
        """
        # Prefer ephemeris elevation if available (more accurate)
        altitude_deg = None
        if ephemeris and ephemeris.elevation_deg is not None:
            altitude_deg = ephemeris.elevation_deg
        elif observability.peak_altitude_deg is not None:
            altitude_deg = observability.peak_altitude_deg

        if altitude_deg is None:
            return 50.0  # Unknown

        if altitude_deg > 60:
            return 100.0
        elif altitude_deg > 45:
            return 80.0 + (altitude_deg - 45) * (20.0 / 15.0)
        elif altitude_deg > 30:
            return 50.0 + (altitude_deg - 30) * (30.0 / 15.0)
        else:
            return max(0.0, altitude_deg * (50.0 / 30.0))

    def _score_time_to_set(
        self, observability: NeoObservability, current_time: datetime
    ) -> float:
        """Time remaining until object sets (more time = better).

        Score breakdown:
        - > 4h remaining: 100 (plenty of time)
        - 2-4h remaining: 70-100 (good)
        - 1-2h remaining: 40-70 (moderate urgency)
        - < 1h remaining: 0-40 (high urgency)
        """
        if not observability.window_end:
            return 50.0  # Unknown

        hours_remaining = (observability.window_end - current_time).total_seconds() / 3600.0

        if hours_remaining > 4:
            return 100.0
        elif hours_remaining > 2:
            return 70.0 + (hours_remaining - 2) * (30.0 / 2.0)
        elif hours_remaining > 1:
            return 40.0 + (hours_remaining - 1) * (30.0 / 1.0)
        else:
            return max(0.0, hours_remaining * 40.0)

    def _score_motion_rate(self, ephemeris: NeoEphemeris | None) -> float:
        """Apparent motion rate score (slower = easier to image).

        Score breakdown:
        - < 10 "/min: 100 (slow mover, easy)
        - 10-30 "/min: 80-100 (moderate)
        - 30-60 "/min: 50-80 (fast, challenging)
        - > 60 "/min: 0-50 (very fast, difficult)
        """
        if not ephemeris or ephemeris.ra_rate_arcsec_min is None:
            return 70.0  # Assume moderate

        # Total plane-of-sky rate
        ra_rate = ephemeris.ra_rate_arcsec_min or 0.0
        dec_rate = ephemeris.dec_rate_arcsec_min or 0.0
        total_rate = math.sqrt(ra_rate**2 + dec_rate**2)  # arcsec/min

        if total_rate < 10:
            return 100.0
        elif total_rate < 30:
            return 80.0 + (30 - total_rate) * (20.0 / 20.0)
        elif total_rate < 60:
            return 50.0 + (60 - total_rate) * (30.0 / 30.0)
        else:
            return max(0.0, 50.0 - (total_rate - 60) * 0.5)

    def _score_uncertainty(
        self, candidate: NeoCandidate, ephemeris: NeoEphemeris | None
    ) -> float:
        """Positional uncertainty score (lower uncertainty = better).

        Score breakdown:
        - < 10": 100 (very precise)
        - 10-30": 80-100 (good)
        - 30-60": 50-80 (moderate)
        - > 60": 0-50 (poor)
        """
        uncertainty = None
        if ephemeris and ephemeris.uncertainty_3sigma_arcsec:
            uncertainty = ephemeris.uncertainty_3sigma_arcsec

        if uncertainty is None:
            return 70.0  # Unknown, assume moderate

        if uncertainty < 10:
            return 100.0
        elif uncertainty < 30:
            return 80.0 + (30 - uncertainty) * (20.0 / 20.0)
        elif uncertainty < 60:
            return 50.0 + (60 - uncertainty) * (30.0 / 30.0)
        else:
            return max(0.0, 50.0 - (uncertainty - 60) * 0.5)

    def _score_arc_extension(
        self, candidate: NeoCandidate, current_time: datetime
    ) -> float:
        """Arc extension value (newer observations = more valuable).

        Orbital determination improves most for short-arc objects.

        Score breakdown:
        - Last obs < 6h ago: 100 (very recent, high value)
        - Last obs 6-24h ago: 70-100 (recent)
        - Last obs 1-3 days ago: 40-70 (moderate)
        - Last obs > 3 days ago: 0-40 (diminishing returns)
        """
        if not candidate.last_obs_utc:
            return 50.0  # Unknown

        hours_since = (current_time - candidate.last_obs_utc).total_seconds() / 3600.0

        if hours_since < 6:
            return 100.0
        elif hours_since < 24:
            return 70.0 + (24 - hours_since) * (30.0 / 18.0)
        elif hours_since < 72:  # 3 days
            return 40.0 + (72 - hours_since) * (30.0 / 48.0)
        else:
            return max(0.0, 40.0 - (hours_since - 72) * (40.0 / 168.0))


__all__ = ["TargetScoringService"]
