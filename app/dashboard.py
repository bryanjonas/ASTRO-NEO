"""Dashboard page routes and partials."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.visualization import ZScaleInterval
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlmodel import select
from sqlalchemy import func

from app.api.session import dashboard_status as session_dashboard_status
from app.db.session import get_session
from app.models import (
    AstrometricSolution,
    EquipmentProfileRecord,
    CaptureLog,
    NeoCandidate,
    NeoObservability,
    SiteConfig,
    SubmissionLog,
)
from app.core.config import settings
from app.services.equipment import (
    EquipmentProfileSpec,
    activate_profile,
    get_active_equipment_profile,
    list_profiles,
    save_profile,
)
from app.services.kpis import KPIService
from app.services.presets import list_presets
from app.services.session import SESSION_STATE
from app.services.night_ops import NightSessionError, kickoff_imaging
from app.services.weather import WeatherService

templates = Jinja2Templates(directory="app/templates")
router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request) -> Any:
    """Render the main dashboard shell."""
    return templates.TemplateResponse(
        "dashboard/index.html",
        {
            "request": request,
        },
    )


def _render_status_panel(
    request: Request,
    status_banner: dict[str, str] | None = None,
    status_code: int = 200,
    bundle: dict | None = None,
) -> HTMLResponse:
    bundle = bundle or session_dashboard_status()
    raw_blockers = bundle.get("bridge_blockers") or []
    ignored = {"camera_exposing", "sequence_running"}
    filtered_blockers = []
    for item in raw_blockers:
        reason = item.get("reason") if isinstance(item, dict) else item
        if reason in ignored:
            continue
        filtered_blockers.append(item)
    bundle = dict(bundle)
    bundle["bridge_blockers_filtered"] = filtered_blockers
    return templates.TemplateResponse(
        "dashboard/partials/status.html",
        {
            "request": request,
            "bundle": bundle,
            "status_banner": status_banner,
        },
        status_code=status_code,
    )


@router.get("/dashboard/partials/status", response_class=HTMLResponse)
def dashboard_status_partial(request: Request) -> Any:
    """HTMX-friendly status bundle."""
    return _render_status_panel(request)


@router.get("/dashboard/partials/captures", response_class=HTMLResponse)
def captures_partial(request: Request) -> Any:
    """Render recent captures from session state (in-memory + DB seeded)."""
    captures = SESSION_STATE.current.captures if SESSION_STATE.current else []
    return templates.TemplateResponse(
        "dashboard/partials/captures.html",
        {"request": request, "captures": captures},
    )


@router.get("/dashboard/partials/solutions", response_class=HTMLResponse)
def solutions_partial(request: Request) -> Any:
    """Render solver view with target selector and per-frame solve status."""
    selected_target = request.query_params.get("target")
    with get_session() as session:
        target_rows = session.exec(
            select(CaptureLog.target, func.count().label("count"), func.max(CaptureLog.started_at).label("latest"))
            .group_by(CaptureLog.target)
            .order_by(func.max(CaptureLog.started_at).desc())
            .limit(30)
        ).all()
        targets = [{"name": row[0], "count": row[1], "latest": row[2]} for row in target_rows]
        if not selected_target and targets:
            selected_target = targets[0]["name"]
        captures = []
        solutions_map: dict[int, AstrometricSolution] = {}
        solver_activity = None
        if selected_target:
            capture_rows = session.exec(
                select(CaptureLog).where(CaptureLog.target == selected_target).order_by(CaptureLog.started_at.desc())
            ).all()
            captures = list(capture_rows)
            capture_ids = [c.id for c in captures if c.id]
            if capture_ids:
                solution_rows = session.exec(
                    select(AstrometricSolution).where(AstrometricSolution.capture_id.in_(capture_ids))
                ).all()
                solutions_map = {row.capture_id: row for row in solution_rows if row.capture_id}
            pending = [c for c in captures if c.id not in solutions_map]
            if pending:
                solver_activity = f"Pending solves: {len(pending)}"
            elif solutions_map:
                solver_activity = "All frames solved for this target."
    return templates.TemplateResponse(
        "dashboard/partials/solutions.html",
        {
            "request": request,
            "targets": targets,
            "selected_target": selected_target,
            "captures": captures,
            "solutions_map": solutions_map,
            "solver_activity": solver_activity,
        },
    )


@router.get("/dashboard/partials/submissions", response_class=HTMLResponse)
def submissions_partial(request: Request) -> Any:
    """Render recent submission log entries."""
    with get_session() as session:
        stmt = select(SubmissionLog).order_by(SubmissionLog.created_at.desc()).limit(10)
        submissions = session.exec(stmt).all()
    return templates.TemplateResponse(
        "dashboard/partials/submissions.html",
        {"request": request, "submissions": submissions},
    )


@router.get("/dashboard/partials/kpis", response_class=HTMLResponse)
def kpis_partial(request: Request) -> Any:
    """Render KPI rollups (7-day window)."""
    svc = KPIService()
    kpis = svc.daily_counts()
    return templates.TemplateResponse(
        "dashboard/partials/kpis.html",
        {"request": request, "kpis": kpis},
    )


@router.get("/dashboard/partials/observatory", response_class=HTMLResponse)
def observatory_partial(request: Request) -> Any:
    """Render site config snapshot."""
    with get_session() as session:
        site = (
            session.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
        )
        weather_service = WeatherService(session)
        weather_summary = weather_service.get_status()
    weather_sources: list[dict[str, Any]] = []
    if site and site.weather_sensors:
        try:
            payload = json.loads(site.weather_sensors)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, list):
            for entry in payload:
                if isinstance(entry, dict):
                    weather_sources.append(entry)
                elif isinstance(entry, str):
                    weather_sources.append({"name": entry, "type": "remote"})
        elif isinstance(payload, dict):
            weather_sources.append(payload)
    return templates.TemplateResponse(
        "dashboard/partials/observatory.html",
        {
            "request": request,
            "site": site,
            "weather_sources": weather_sources,
            "weather_summary": weather_summary,
        },
    )


@router.get("/dashboard/partials/equipment", response_class=HTMLResponse)
def equipment_partial(request: Request) -> Any:
    """Render active equipment profile summary."""
    profile = get_active_equipment_profile()
    profiles = list_profiles()
    return templates.TemplateResponse(
        "dashboard/partials/equipment.html",
        {"request": request, "profile": profile, "profiles": profiles},
    )


def _load_targets(limit: int = 20) -> list[dict[str, Any]]:
    with get_session() as session:
        stmt = (
            select(NeoObservability, NeoCandidate)
            .join(NeoCandidate, NeoCandidate.id == NeoObservability.candidate_id)
            .order_by(NeoObservability.score.desc())
            .limit(limit)
        )
        rows = session.exec(stmt).all()
    return [
        {
            "trksub": obs.trksub,
            "score": obs.score,
            "is_observable": obs.is_observable,
            "duration_minutes": obs.duration_minutes,
            "window_start": obs.window_start,
            "window_end": obs.window_end,
            "max_altitude_deg": obs.max_altitude_deg,
            "min_moon_separation_deg": obs.min_moon_separation_deg,
            "vmag": cand.vmag,
        }
        for obs, cand in rows
    ]


def _render_targets_partial(
    request: Request,
    targets: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> HTMLResponse:
    if targets is None:
        targets = _load_targets()
    return templates.TemplateResponse(
        "dashboard/partials/targets.html",
        {
            "request": request,
            "targets": targets,
            "target_mode": SESSION_STATE.target_mode,
            "selected_target": SESSION_STATE.selected_target,
            "error": error,
        },
    )


@router.get("/dashboard/partials/targets", response_class=HTMLResponse)
def targets_partial(request: Request) -> Any:
    """Render top observability-ranked targets."""
    return _render_targets_partial(request)


def _bridge_is_ready(bundle: dict[str, Any]) -> bool:
    ready_flags = bundle.get("bridge_ready") or {}
    return bool(ready_flags) and ready_flags.get("ready_to_slew") and ready_flags.get("ready_to_expose")


def _generate_fits_preview(path: str) -> str:
    fits_path = Path(path)
    if not fits_path.exists():
        raise FileNotFoundError("Capture file missing.")
    data = fits.getdata(fits_path)
    if data is None:
        raise ValueError("No data in FITS frame.")
    if data.ndim > 2:
        data = data[0]
    interval = ZScaleInterval()
    vmin, vmax = interval.get_limits(data)
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmin, vmax = np.nanmin(data), np.nanmax(data)
        if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
            raise ValueError("Unable to scale FITS data.")
    clipped = np.clip(data, vmin, vmax)
    norm = (clipped - vmin) / (vmax - vmin)
    image_array = (norm * 255).astype(np.uint8)
    img = Image.fromarray(image_array)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@router.get("/dashboard/partials/reports", response_class=HTMLResponse)
def reports_partial(request: Request) -> Any:
    """Render submission log for the Reports tab."""
    with get_session() as session:
        stmt = select(SubmissionLog).order_by(SubmissionLog.created_at.desc()).limit(15)
        submissions = session.exec(stmt).all()
    return templates.TemplateResponse(
        "dashboard/partials/reports.html",
        {"request": request, "submissions": submissions},
    )


def _render_exposure_partial(
    request: Request,
    presets: list,
    selected_preset: dict[str, Any] | None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    effective_selected = selected_preset
    if effective_selected is None and presets:
        default = _default_preset(presets)
        if default:
            effective_selected = SESSION_STATE.select_preset(default)
    return templates.TemplateResponse(
        "dashboard/partials/exposure_config.html",
        {"request": request, "presets": presets, "selected_preset": effective_selected, "error": error},
        status_code=status_code,
    )


@router.get("/dashboard/partials/exposure_config", response_class=HTMLResponse)
def exposure_config_partial(request: Request) -> Any:
    """Render exposure preset configuration."""
    profile = get_active_equipment_profile()
    presets = list(list_presets(profile))
    return _render_exposure_partial(request, presets, SESSION_STATE.selected_preset)


@router.get("/dashboard/partials/session_status", response_class=HTMLResponse)
def session_status_partial_panel(request: Request) -> Any:
    """Render session status for the exposures tab."""
    session_info = {"active": False}
    if SESSION_STATE.current:
        session_info = SESSION_STATE.current.to_dict()
        session_info["active"] = True
    return templates.TemplateResponse(
        "dashboard/partials/session_status.html",
        {"request": request, "session": session_info},
    )


@router.get("/dashboard/partials/association", response_class=HTMLResponse)
def association_partial(request: Request) -> Any:
    """Association workflow view: select target and annotate centroids."""
    captures = SESSION_STATE.current.captures if SESSION_STATE.current else []
    targets: list[dict[str, Any]] = []
    by_target: dict[str, list[dict[str, Any]]] = {}
    for cap in captures:
        tgt = cap.get("target") or "unknown"
        by_target.setdefault(tgt, []).append(cap)
    for tgt, items in by_target.items():
        latest = items[-1]["started_at"] if items else None
        targets.append({"name": tgt, "count": len(items), "latest": latest})
    targets = sorted(targets, key=lambda t: t["latest"] or "", reverse=True)
    selected_target = request.query_params.get("target")
    if not selected_target and targets:
        selected_target = targets[0]["name"]
    selected_captures = by_target.get(selected_target, []) if selected_target else []
    associations = SESSION_STATE.associations if SESSION_STATE else {}
    return templates.TemplateResponse(
        "dashboard/partials/association.html",
        {
            "request": request,
            "targets": targets,
            "selected_target": selected_target,
            "captures": selected_captures,
            "associations": associations,
        },
    )


@router.post("/dashboard/association/manual", response_class=HTMLResponse)
async def association_manual(request: Request) -> Any:
    """Record a manual centroid for a capture path."""
    form = await request.form()
    path = form.get("path")
    ra = form.get("ra_deg")
    dec = form.get("dec_deg")
    error = None
    if not path or not ra or not dec:
        error = "Provide path, RA, and Dec."
    else:
        try:
            ra_f = float(ra)
            dec_f = float(dec)
            SESSION_STATE.set_association(path, ra_f, dec_f)
        except Exception:
            error = "Invalid RA/Dec values."
    # Re-render association partial
    return association_partial(request)


@router.get("/dashboard/partials/masters", response_class=HTMLResponse)
def masters_partial(request: Request) -> Any:
    """Render master calibration upload/selection pane."""
    master_root = Path("/data/masters")
    types = ["bias", "dark", "flat"]
    existing: dict[str, list[str]] = {}
    for t in types:
        paths = []
        for p in (master_root / t).glob("*"):
            if p.is_file():
                paths.append(str(p))
        existing[t] = sorted(paths)
    selected = SESSION_STATE.master_calibrations if SESSION_STATE else {}
    return templates.TemplateResponse(
        "dashboard/partials/masters.html",
        {"request": request, "existing": existing, "selected": selected},
    )


@router.post("/dashboard/masters/upload", response_class=HTMLResponse)
async def masters_upload(request: Request) -> Any:
    form = await request.form()
    cal_type = (form.get("cal_type") or "").lower()
    file = form.get("file")
    if cal_type not in {"bias", "dark", "flat"} or not file:
        return masters_partial(request)
    master_root = Path("/data/masters") / cal_type
    master_root.mkdir(parents=True, exist_ok=True)
    dest = master_root / (file.filename or f"{cal_type}.fits")
    content = await file.read()
    dest.write_bytes(content)
    SESSION_STATE.set_master(cal_type, str(dest))
    return masters_partial(request)


@router.post("/dashboard/masters/select", response_class=HTMLResponse)
async def masters_select(request: Request) -> Any:
    form = await request.form()
    cal_type = (form.get("cal_type") or "").lower()
    path = form.get("path")
    if cal_type and path:
        SESSION_STATE.set_master(cal_type, path)
    return masters_partial(request)


@router.post("/dashboard/exposure/select", response_class=HTMLResponse)
async def exposure_select(request: Request) -> Any:
    """HTMX handler to mark a preset as active for the current session."""
    form = await request.form()
    preset_name = (form.get("preset_name") or "").strip().lower()
    profile = get_active_equipment_profile()
    presets = list(list_presets(profile))
    if not preset_name:
        return _render_exposure_partial(
            request,
            presets,
            SESSION_STATE.selected_preset,
            error="Select a preset to activate.",
            status_code=400,
        )
    chosen = next((preset for preset in presets if preset.name.lower() == preset_name), None)
    if not chosen:
        return _render_exposure_partial(
            request,
            presets,
            SESSION_STATE.selected_preset,
            error="Preset not found or unavailable for the active equipment profile.",
            status_code=404,
        )
    SESSION_STATE.select_preset(chosen)
    return _render_exposure_partial(request, presets, SESSION_STATE.selected_preset)


@router.post("/dashboard/exposure/config", response_class=HTMLResponse)
async def exposure_config_update(request: Request) -> Any:
    """Allow editing of the active imaging configuration."""
    form = await request.form()
    profile = get_active_equipment_profile()
    presets = list(list_presets(profile))
    error = None
    try:
        exposure_seconds = float(form.get("exposure_seconds") or 0)
        count = int(form.get("count") or 0)
        delay_seconds = float(form.get("delay_seconds") or 0)
        binning = int(form.get("binning") or 1)
        filter_name = (form.get("filter") or "").strip() or "L"
        if exposure_seconds <= 0 or count <= 0 or delay_seconds < 0 or binning <= 0:
            raise ValueError("invalid_range")
        SESSION_STATE.update_preset_config(
            exposure_seconds=exposure_seconds,
            count=count,
            delay_seconds=delay_seconds,
            binning=binning,
            filter_name=filter_name,
        )
    except ValueError as exc:
        if str(exc) == "no_preset_selected":
            error = "Select a preset before editing the imaging configuration."
        else:
            error = "Provide positive values for exposure, count, spacing, and binning."
    return _render_exposure_partial(request, presets, SESSION_STATE.selected_preset, error=error)


@router.post("/dashboard/night/start", response_class=HTMLResponse)
def night_start(request: Request) -> Any:
    """Convenience button on Live tab to kick off nightly session prep."""
    bundle = session_dashboard_status()
    if not _bridge_is_ready(bundle):
        return _render_status_panel(
            request,
            status_banner={
                "kind": "warn",
                "text": "Bridge is not ready to image (check connections, blockers, or manual override).",
            },
            status_code=400,
            bundle=bundle,
        )
    if SESSION_STATE.current:
        return _render_status_panel(
            request,
            status_banner={"kind": "info", "text": "Session already running. Use Pause or End to change state."},
            bundle=bundle,
        )
    SESSION_STATE.start(notes="night-start")
    try:
        kickoff_imaging()
    except NightSessionError as exc:
        SESSION_STATE.end()
        return _render_status_panel(
            request,
            status_banner={"kind": "warn", "text": exc.message},
            status_code=400,
        )
    return _render_status_panel(
        request,
        status_banner={"kind": "good", "text": "Night session launched — automation running."},
    )


@router.post("/dashboard/night/pause", response_class=HTMLResponse)
def night_pause(request: Request) -> Any:
    """Toggle pause/resume state for the current session."""
    if not SESSION_STATE.current:
        return _render_status_panel(
            request,
            status_banner={"kind": "warn", "text": "No active session to pause."},
            status_code=400,
        )
    if SESSION_STATE.current.paused:
        SESSION_STATE.resume()
        banner = {"kind": "info", "text": "Session resumed — automation can continue."}
    else:
        SESSION_STATE.pause()
        banner = {"kind": "info", "text": "Session paused — automation temporarily halted."}
    return _render_status_panel(request, status_banner=banner)


@router.post("/dashboard/night/end", response_class=HTMLResponse)
def night_end(request: Request) -> Any:
    """End the current session."""
    if not SESSION_STATE.current:
        return _render_status_panel(
            request,
            status_banner={"kind": "warn", "text": "No active session to end."},
            status_code=400,
        )
    SESSION_STATE.end()
    return _render_status_panel(
        request,
        status_banner={"kind": "info", "text": "Session ended. Start again when ready."},
    )


@router.post("/dashboard/capture/delete", response_class=HTMLResponse)
async def capture_delete(request: Request) -> Any:
    """Delete a capture log entry and associated file/solution."""
    form = await request.form()
    path = form.get("path")
    if not path:
        return templates.TemplateResponse(
            "dashboard/partials/captures.html",
            {"request": request, "captures": SESSION_STATE.current.captures if SESSION_STATE.current else []},
            status_code=400,
        )
    # Remove from DB and solutions
    with get_session() as session:
        rows = session.exec(select(CaptureLog).where(CaptureLog.path == path)).all()
        capture_ids = [row.id for row in rows if row.id]
        if capture_ids:
            session.exec(select(AstrometricSolution).where(AstrometricSolution.capture_id.in_(capture_ids))).all()
            session.exec(AstrometricSolution.__table__.delete().where(AstrometricSolution.capture_id.in_(capture_ids)))
        session.exec(CaptureLog.__table__.delete().where(CaptureLog.path == path))
        session.commit()
    # Remove file on disk
    try:
        fits_path = Path(path)
        if fits_path.exists():
            fits_path.unlink()
    except Exception:
        pass
    # Remove from in-memory session captures
    if SESSION_STATE.current:
        SESSION_STATE.current.captures = [c for c in SESSION_STATE.current.captures if c.get("path") != path]
    captures = SESSION_STATE.current.captures if SESSION_STATE.current else []
    return templates.TemplateResponse(
        "dashboard/partials/captures.html",
        {"request": request, "captures": captures},
    )


def _default_preset(presets: list) -> Any | None:
    for preset in presets:
        if getattr(preset, "name", "").lower() == "bright":
            return preset
    return presets[0] if presets else None


@router.get("/dashboard/partials/capture_viewer", response_class=HTMLResponse)
def capture_viewer_partial(request: Request, path: str | None = None, target: str | None = None, index: str | None = None, started_at: str | None = None) -> Any:
    """Render a lightweight FITS preview for the selected capture."""
    preview = None
    error = None
    meta = {"target": target, "index": index, "started_at": started_at, "path": path}
    if path:
        try:
            preview = _generate_fits_preview(path)
        except Exception as exc:  # noqa: BLE001
            error = f"Unable to render FITS preview: {exc}"
    return templates.TemplateResponse(
        "dashboard/partials/capture_viewer.html",
        {
            "request": request,
            "preview": preview,
            "error": error,
            "meta": meta,
        },
    )


@router.post("/dashboard/targets/mode", response_class=HTMLResponse)
async def targets_mode(request: Request) -> Any:
    """Toggle between auto and manual target selection."""
    form = await request.form()
    mode = (form.get("mode") or "").strip().lower()
    error = None
    try:
        SESSION_STATE.set_target_mode(mode)
    except ValueError:
        error = "Unsupported mode."
    return _render_targets_partial(request, error=error)


@router.post("/dashboard/targets/select", response_class=HTMLResponse)
async def targets_select(request: Request) -> Any:
    """Select a specific target for manual mode."""
    form = await request.form()
    trksub = (form.get("trksub") or "").strip()
    error = None
    targets = _load_targets()
    if not trksub:
        error = "Choose a target to select."
    elif not any(t["trksub"] == trksub for t in targets):
        error = "Target is no longer in the visible list."
    else:
        SESSION_STATE.select_target(trksub)
    return _render_targets_partial(request, targets=targets, error=error)


@router.post("/dashboard/observatory/save", response_class=HTMLResponse)
async def observatory_save(request: Request) -> Any:
    """Persist observatory settings from the dashboard form."""
    form = await request.form()
    with get_session() as session:
        existing = session.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
    horizon_mask_json = form.get("horizon_mask_json") or None
    parsed_horizon = None
    error = None
    if horizon_mask_json:
        import json

        try:
            parsed = json.loads(horizon_mask_json)

            # Validate PVGIS format (preferred)
            if isinstance(parsed, dict) and "outputs" in parsed:
                horizon_profile = parsed.get("outputs", {}).get("horizon_profile", [])
                if not isinstance(horizon_profile, list):
                    raise ValueError("PVGIS format requires outputs.horizon_profile to be a list")
                if horizon_profile and not all(
                    isinstance(p, dict) and "A" in p and "H_hor" in p for p in horizon_profile
                ):
                    raise ValueError("PVGIS horizon_profile entries must have 'A' and 'H_hor' keys")
                parsed_horizon = horizon_mask_json

            # Validate simple format (legacy)
            elif isinstance(parsed, list):
                if any(
                    not isinstance(p, dict) or "az_deg" not in p or "alt_deg" not in p for p in parsed
                ):
                    raise ValueError("Simple format requires a list of objects with az_deg and alt_deg")
                parsed_horizon = horizon_mask_json

            else:
                raise ValueError(
                    "Expected either PVGIS format (object with outputs.horizon_profile) "
                    "or simple format (list of {az_deg, alt_deg})"
                )
        except Exception as exc:  # noqa: BLE001
            error = f"Horizon mask JSON invalid: {exc}"

    horizon_mask_path = None
    if form.get("horizon_mask_path"):
        horizon_mask_path = form.get("horizon_mask_path")
    elif existing:
        horizon_mask_path = existing.horizon_mask_path

    payload = {
        "name": settings.site_name,
        "latitude": float(form.get("latitude") or 0.0),
        "longitude": float(form.get("longitude") or 0.0),
        "altitude_m": float(form.get("altitude_m") or 0.0),
        "bortle": int(form.get("bortle")) if form.get("bortle") else None,
        "horizon_mask_path": horizon_mask_path,
        "horizon_mask_json": parsed_horizon,
        "weather_sensors": form.get("weather_sensors") or None,
    }
    if error:
        return templates.TemplateResponse(
            "dashboard/partials/observatory.html",
            {"request": request, "site": existing, "error": error},
        )
    with get_session() as session:
        record = existing
        if record:
            for k, v in payload.items():
                setattr(record, k, v)
            session.add(record)
            session.commit()
            session.refresh(record)
        else:
            record = SiteConfig(**payload)
            session.add(record)
            session.commit()
            session.refresh(record)
    return templates.TemplateResponse(
        "dashboard/partials/observatory.html",
        {"request": request, "site": record, "saved": True},
    )


def _parse_list_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@router.post("/dashboard/equipment/save", response_class=HTMLResponse)
async def equipment_save(request: Request) -> Any:
    """Create/update an equipment profile from the dashboard form."""
    form = await request.form()
    name = form.get("name") or "default"
    camera = {
        "type": form.get("camera_type") or "mono",
        "filters": _parse_list_csv(form.get("camera_filters")),
        "max_binning": int(form.get("camera_max_binning") or 1),
        "gain_presets": {},
        "offset_presets": {},
    }
    focuser = None
    if form.get("focuser_min") or form.get("focuser_max"):
        focuser = {
            "position_min": int(form.get("focuser_min") or 0),
            "position_max": int(form.get("focuser_max") or 0),
        }
    mount = {"supports_parking": form.get("mount_supports_parking") == "on"}
    presets = []
    if form.get("preset_name"):
        presets.append(
            {
                "name": form.get("preset_name"),
                "exposure": float(form.get("preset_exposure") or 0),
                "binning": int(form.get("preset_binning") or 1),
                "filter": form.get("preset_filter") or None,
            }
        )
    spec = EquipmentProfileSpec(camera=camera, focuser=focuser, mount=mount, presets=presets)
    record = save_profile(name=name, payload=spec, activate=form.get("activate") == "on")
    profiles = list_profiles()
    return templates.TemplateResponse(
        "dashboard/partials/equipment.html",
        {"request": request, "profile": get_active_equipment_profile(), "profiles": profiles, "saved": True},
    )


@router.post("/dashboard/equipment/activate", response_class=HTMLResponse)
async def equipment_activate(request: Request) -> Any:
    """Activate a saved profile from the dashboard."""
    form = await request.form()
    profile_id = int(form.get("profile_id"))
    activate_profile(profile_id)
    profiles = list_profiles()
    return templates.TemplateResponse(
        "dashboard/partials/equipment.html",
        {"request": request, "profile": get_active_equipment_profile(), "profiles": profiles, "saved": True},
    )


__all__ = ["router"]
