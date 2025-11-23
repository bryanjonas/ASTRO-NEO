"""Dashboard page routes and partials."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select

from app.api.session import dashboard_status as session_dashboard_status
from app.api.retention import retention_status
from app.db.session import get_session
from app.models import (
    AstrometricSolution,
    EquipmentProfileRecord,
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
    retention: dict | None = None,
) -> HTMLResponse:
    bundle = bundle or session_dashboard_status()
    retention = retention or retention_status()
    return templates.TemplateResponse(
        "dashboard/partials/status.html",
        {
            "request": request,
            "bundle": bundle,
            "retention": retention,
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
    """Render recent astrometry solutions plus submissions and KPIs in one shot."""
    with get_session() as session:
        stmt = (
            select(AstrometricSolution)
            .order_by(AstrometricSolution.solved_at.desc())
            .limit(15)
        )
        rows = session.exec(stmt).all()
        sub_stmt = select(SubmissionLog).order_by(SubmissionLog.created_at.desc()).limit(10)
        submissions = session.exec(sub_stmt).all()
    kpis = KPIService().daily_counts()
    solutions = [
        {
            "id": row.id,
            "capture_id": row.capture_id,
            "measurement_id": getattr(row, "measurement_id", None),
            "path": row.path,
            "ra_deg": row.ra_deg,
            "dec_deg": row.dec_deg,
            "uncertainty_arcsec": row.uncertainty_arcsec,
            "snr": getattr(row, "snr", None),
            "mag_inst": getattr(row, "mag_inst", None),
            "flags": row.flags,
            "solved_at": row.solved_at,
            "success": row.success,
            "target": row.target,
        }
        for row in rows
    ]
    return templates.TemplateResponse(
        "dashboard/partials/solutions.html",
        {
            "request": request,
            "solutions": solutions,
            "submissions": submissions,
            "kpis": kpis,
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


@router.post("/dashboard/night/start", response_class=HTMLResponse)
def night_start(request: Request) -> Any:
    """Convenience button on Live tab to kick off nightly session prep."""
    bundle = session_dashboard_status()
    retention = retention_status()
    if not _bridge_is_ready(bundle):
        return _render_status_panel(
            request,
            status_banner={
                "kind": "warn",
                "text": "Bridge is not ready to image (check connections, blockers, or manual override).",
            },
            status_code=400,
            bundle=bundle,
            retention=retention,
        )
    if SESSION_STATE.current:
        return _render_status_panel(
            request,
            status_banner={"kind": "info", "text": "Session already running. Use Pause or End to change state."},
            bundle=bundle,
            retention=retention,
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


def _default_preset(presets: list) -> Any | None:
    for preset in presets:
        if getattr(preset, "name", "").lower() == "bright":
            return preset
    return presets[0] if presets else None


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
