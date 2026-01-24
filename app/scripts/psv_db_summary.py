"""Summarize PSV-related database stats for quick CLI checks."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from app.db.session import get_session
from app.models import Measurement, CaptureLog, CandidateAssociation, NeoCandidate


def _utc_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()


def _normalize_target_id(target: str) -> str:
    cleaned = target.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    return cleaned


def main() -> None:
    with get_session() as session:
        measurements = session.exec(select(Measurement)).all()
        captures = session.exec(select(CaptureLog).where(CaptureLog.kind == "science")).all()
        associations = session.exec(select(CandidateAssociation)).all()
        candidates = session.exec(select(NeoCandidate)).all()

    candidate_map = {}
    for cand in candidates:
        if cand.id:
            candidate_map[cand.id] = cand
        if cand.trksub:
            candidate_map[cand.trksub] = cand

    association_ids = {assoc.capture_id for assoc in associations if assoc.capture_id}

    targets = {}
    for row in measurements:
        target = row.target
        if target not in targets:
            normalized = _normalize_target_id(target)
            candidate = candidate_map.get(normalized) or candidate_map.get(target)
            targets[target] = {
                "target": target,
                "object_number": normalized if normalized.isdigit() else target,
                "vmag": candidate.vmag if candidate else None,
                "total_obs": 0,
                "first_obs": None,
                "last_obs": None,
                "nights": {},
            }
        entry = targets[target]
        entry["total_obs"] += 1
        obs_time = row.obs_time
        if entry["first_obs"] is None or obs_time < entry["first_obs"]:
            entry["first_obs"] = obs_time
        if entry["last_obs"] is None or obs_time > entry["last_obs"]:
            entry["last_obs"] = obs_time

        night_key = _utc_date(obs_time)
        night = entry["nights"].setdefault(night_key, {"count": 0, "mag_count": 0})
        night["count"] += 1
        if row.magnitude is not None:
            night["mag_count"] += 1

    exposure_stats = {}
    for cap in captures:
        stats = exposure_stats.setdefault(
            cap.target,
            {"science_exposures": 0, "solved": 0, "associated": 0},
        )
        stats["science_exposures"] += 1
        if cap.has_wcs:
            stats["solved"] += 1
        if cap.id in association_ids:
            stats["associated"] += 1

    print(f"Measurements: {len(measurements)}")
    print(f"Science captures: {len(captures)}")
    print(f"Associations: {len(associations)}")
    print("")
    print("Targets:")

    for key in sorted(targets.keys()):
        entry = targets[key]
        nights = entry["nights"]
        night_keys = sorted(nights.keys())
        qualifying_nights = [
            night_key
            for night_key in night_keys
            if 3 <= nights[night_key]["count"] <= 5 and nights[night_key]["mag_count"] >= 1
        ]
        vmag_ok = entry["vmag"] is not None and entry["vmag"] > 16.0
        numbered_ok = str(entry["object_number"]).isdigit()
        ready = len(qualifying_nights) >= 2 and vmag_ok and numbered_ok
        stats = exposure_stats.get(key, {"science_exposures": 0, "solved": 0, "associated": 0})
        vmag_display = "--" if entry["vmag"] is None else f"{entry['vmag']:.2f}"
        print(
            f"- {entry['object_number']} vmag={vmag_display} "
            f"obs={entry['total_obs']} nights={len(night_keys)} "
            f"ready={'Y' if ready else 'N'} "
            f"science={stats['science_exposures']} solved={stats['solved']} assoc={stats['associated']}"
        )


if __name__ == "__main__":
    main()
