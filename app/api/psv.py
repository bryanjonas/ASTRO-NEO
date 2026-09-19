"""PSV readiness and bundle endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.core.config import settings
from app.db.session import get_session_dep
from app.models import Measurement, CaptureLog, CandidateAssociation, NeoCandidate
from app.services.reporting import ReportService

router = APIRouter(prefix="/psv", tags=["psv"])

# Association quality grades (from AnalysisService.find_best_match_scored,
# via CandidateAssociation.quality_grade) accepted toward PSV readiness and
# bundle generation. An unscored association (older distance-only fallback
# match, or no association at all) doesn't count -- "ready" should mean the
# data is good, not just that enough frames exist.
ACCEPTED_QUALITY_GRADES = {"A", "B"}


class PsvBundleRequest(BaseModel):
    targets: list[str] = Field(default_factory=list)
    bundle_label: str | None = None


def _utc_date(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date().isoformat()

def _normalize_target_id(target: str) -> str:
    cleaned = target.strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = cleaned[1:-1]
    return cleaned


@router.get("/targets")
def list_psv_targets(
    db: Session = Depends(get_session_dep),
) -> dict[str, Any]:
    measurements = db.exec(select(Measurement)).all()
    captures = db.exec(select(CaptureLog).where(CaptureLog.kind == "science")).all()
    associations = db.exec(select(CandidateAssociation)).all()
    candidates = db.exec(select(NeoCandidate)).all()

    candidate_map: dict[str, NeoCandidate] = {}
    for cand in candidates:
        if cand.id:
            candidate_map[cand.id] = cand
        if cand.trksub:
            candidate_map[cand.trksub] = cand

    association_ids = {assoc.capture_id for assoc in associations if assoc.capture_id}
    quality_by_capture: dict[int, str | None] = {
        assoc.capture_id: assoc.quality_grade for assoc in associations if assoc.capture_id
    }
    targets: dict[str, dict[str, Any]] = {}

    for row in measurements:
        target = row.target
        if target not in targets:
            normalized = _normalize_target_id(target)
            candidate = candidate_map.get(normalized) or candidate_map.get(target)
            targets[target] = {
                "target": target,
                "object_number": normalized if normalized.isdigit() else target,
                "vmag": candidate.vmag if candidate else None,
                "vmag_sum": 0.0,
                "vmag_count": 0,
                "total_obs": 0,
                "good_obs": 0,
                "first_obs": None,
                "last_obs": None,
                "nights": {},
            }
        entry = targets[target]
        entry["total_obs"] += 1
        if row.magnitude is not None:
            entry["vmag_sum"] += row.magnitude
            entry["vmag_count"] += 1

        is_good = quality_by_capture.get(row.capture_id) in ACCEPTED_QUALITY_GRADES
        if is_good:
            entry["good_obs"] += 1

        obs_time = row.obs_time
        if entry["first_obs"] is None or obs_time < entry["first_obs"]:
            entry["first_obs"] = obs_time
        if entry["last_obs"] is None or obs_time > entry["last_obs"]:
            entry["last_obs"] = obs_time

        night_key = _utc_date(obs_time)
        night = entry["nights"].setdefault(
            night_key,
            {"count": 0, "mag_count": 0, "good_count": 0, "good_mag_count": 0},
        )
        night["count"] += 1
        if row.magnitude is not None:
            night["mag_count"] += 1
        if is_good:
            night["good_count"] += 1
            if row.magnitude is not None:
                night["good_mag_count"] += 1

    payload = []
    for entry in targets.values():
        nights = entry["nights"]
        night_keys = sorted(nights.keys())
        per_night = [
            {
                "night": night_key,
                "count": nights[night_key]["count"],
                "mag_count": nights[night_key]["mag_count"],
                "good_count": nights[night_key]["good_count"],
                "good_mag_count": nights[night_key]["good_mag_count"],
            }
            for night_key in night_keys
        ]
        # Quality-gated: a night only qualifies if enough of its frames are
        # good-quality associations (grade A/B), not just present. A night
        # with 5 frames where only 1 is grade A doesn't count.
        qualifying_nights = [
            night
            for night in per_night
            if 3 <= night["good_count"] <= 5 and night["good_mag_count"] >= 1
        ]
        measured_vmag = None
        if entry["vmag_count"] > 0:
            measured_vmag = entry["vmag_sum"] / entry["vmag_count"]
        vmag_value = entry["vmag"] if entry["vmag"] is not None else measured_vmag
        vmag_ok = vmag_value is not None and vmag_value > 16.0
        numbered_ok = str(entry["object_number"]).isdigit()
        ready = len(qualifying_nights) >= 2 and vmag_ok and numbered_ok
        payload.append(
            {
                "target": entry["target"],
                "object_number": entry["object_number"],
                "vmag": vmag_value,
                "total_obs": entry["total_obs"],
                "good_obs": entry["good_obs"],
                "nights_observed": len(night_keys),
                "qualifying_nights": len(qualifying_nights),
                "ready": ready,
                "per_night": per_night,
                "first_obs": entry["first_obs"].isoformat() if entry["first_obs"] else None,
                "last_obs": entry["last_obs"].isoformat() if entry["last_obs"] else None,
            }
        )

    exposure_stats: dict[str, dict[str, int]] = {}
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

    for entry in payload:
        stats = exposure_stats.get(
            entry["target"],
            {"science_exposures": 0, "solved": 0, "associated": 0},
        )
        entry["science_exposures"] = stats["science_exposures"]
        entry["solved"] = stats["solved"]
        entry["associated"] = stats["associated"]

    payload.sort(key=lambda item: item["target"])
    return {"targets": payload}


@router.post("/bundle")
def create_psv_bundle(
    request: PsvBundleRequest,
    db: Session = Depends(get_session_dep),
) -> dict[str, Any]:
    targets = [t.strip() for t in request.targets if t.strip()]
    if not targets:
        raise HTTPException(status_code=400, detail="No targets provided")

    measurements = db.exec(
        select(Measurement)
        .join(CaptureLog, Measurement.capture_id == CaptureLog.id)
        .join(CandidateAssociation, CandidateAssociation.capture_id == CaptureLog.id)
        .where(Measurement.target.in_(targets))
        .where(CaptureLog.has_wcs == True)
        .where(CandidateAssociation.quality_grade.in_(ACCEPTED_QUALITY_GRADES))
    ).all()
    if not measurements:
        raise HTTPException(
            status_code=404,
            detail="No quality-gated measurements found for targets "
            f"(grade {'/'.join(sorted(ACCEPTED_QUALITY_GRADES))} required)",
        )

    bundle_label = request.bundle_label or "MULTI"
    report_service = ReportService(db)
    bundle = report_service.write_ades_psv_bundle(
        measurements,
        bundle_label=bundle_label,
    )
    bundle["targets"] = targets
    return bundle


@router.get("/files")
def list_psv_files(
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    output_dir = Path(settings.psv_output_dir)
    if not output_dir.exists():
        return {"files": []}

    psv_files = sorted(
        output_dir.glob("*.ades.psv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    payload = []
    for path in psv_files[:limit]:
        payload.append(
            {
                "name": path.name,
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "modified_at": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z",
            }
        )
    return {"files": payload}


@router.get("/download/{filename}")
def download_psv(filename: str) -> FileResponse:
    output_dir = Path(settings.psv_output_dir).resolve()
    file_path = (output_dir / filename).resolve()
    if output_dir not in file_path.parents:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=file_path.name)


__all__ = ["router"]
