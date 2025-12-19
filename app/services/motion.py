"""Helpers for estimating apparent motion rates from cached ephemerides."""

from __future__ import annotations

import math
from typing import Optional

from sqlmodel import Session, select

from app.models import NeoEphemeris


def estimate_motion_rate_arcsec_per_min(
    session: Session,
    candidate_id: Optional[str],
) -> Optional[float]:
    """Return the most recent apparent motion rate for a candidate.

    Args:
        session: Database session
        candidate_id: NeoCandidate ID

    Returns:
        Motion rate in arcsec/min if available, otherwise None.
    """
    if not candidate_id:
        return None

    row = session.exec(
        select(NeoEphemeris)
        .where(NeoEphemeris.candidate_id == candidate_id)
        .order_by(NeoEphemeris.epoch.desc())
    ).first()

    if not row:
        return None

    if row.rate_arcsec_per_min:
        return row.rate_arcsec_per_min

    if row.ra_rate_arcsec_min is not None and row.dec_rate_arcsec_min is not None:
        return float(math.hypot(row.ra_rate_arcsec_min, row.dec_rate_arcsec_min))

    return None


__all__ = ["estimate_motion_rate_arcsec_per_min"]
