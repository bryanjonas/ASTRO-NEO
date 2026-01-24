"""One-time backfill for Measurement.magnitude using nearest ephemeris."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from app.db.session import get_session
from app.models import Measurement, NeoEphemeris


def _normalize_target_id(target: str) -> str:
    cleaned = target.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    return cleaned


def _nearest_ephemeris(
    session,
    candidate_id: str,
    when: datetime,
) -> NeoEphemeris | None:
    ephems = session.exec(
        select(NeoEphemeris)
        .where(NeoEphemeris.candidate_id == candidate_id)
        .order_by(NeoEphemeris.epoch)
    ).all()
    if not ephems:
        return None
    best = None
    best_diff = float("inf")
    for eph in ephems:
        diff = abs((eph.epoch - when).total_seconds())
        if diff < best_diff:
            best = eph
            best_diff = diff
    return best


def main() -> None:
    updated = 0
    skipped = 0

    with get_session() as session:
        rows = session.exec(select(Measurement).where(Measurement.magnitude.is_(None))).all()
        for row in rows:
            target = row.target or ""
            candidate_id = _normalize_target_id(target)
            obs_time = row.obs_time
            if obs_time.tzinfo is None:
                obs_time = obs_time.replace(tzinfo=timezone.utc)
            eph = _nearest_ephemeris(session, candidate_id, obs_time)
            if not eph:
                skipped += 1
                continue
            mag = eph.v_mag_predicted if eph.v_mag_predicted is not None else eph.magnitude
            if mag is None:
                skipped += 1
                continue
            row.magnitude = float(mag)
            updated += 1
        session.commit()

    print(f"Backfill complete. Updated={updated} Skipped={skipped}")


if __name__ == "__main__":
    main()
