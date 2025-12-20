"""Dashboard page routes and partials."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
import logging
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.visualization import ZScaleInterval
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from PIL import Image
from sqlmodel import Session, select, update
from sqlalchemy import func

from app.services.nina_client import NinaBridgeService

from app.api.session import dashboard_status as session_dashboard_status
from app.db.session import get_session
from app.models import (
    AstrometricSolution,
    CandidateAssociation,
    EquipmentProfileRecord,
    CaptureLog,
    NeoCandidate,
    NeoEphemeris,
    NeoObservability,
    Measurement,
    SubmissionLog,
    SiteConfig,
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
from app.services.session import SESSION_STATE
from app.services.night_ops import NightSessionError, kickoff_imaging
from app.services.weather import WeatherService

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["basename"] = lambda p: Path(p).name if p else ""
router = APIRouter()
logger = logging.getLogger(__name__)


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
    oob: bool = False,
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
            "oob": oob,
            "timezone": SESSION_STATE.timezone,
        },
        status_code=status_code,
    )


@router.get("/dashboard/partials/status", response_class=HTMLResponse)
def dashboard_status_partial(request: Request) -> Any:
    """HTMX-friendly status bundle."""
    import logging
    logging.getLogger("uvicorn").info("Overview status update requested")
    return _render_status_panel(request)


@router.post("/dashboard/weather/override", response_class=HTMLResponse)
def weather_override(request: Request, ignore: bool = Form(...)) -> Any:
    """Toggle weather override."""
    from app.services.nina_client import NinaBridgeService
    bridge = NinaBridgeService()
    bridge.set_ignore_weather(ignore)
    return _render_status_panel(request)


@router.get("/dashboard/partials/captures", response_class=HTMLResponse)
def captures_partial(request: Request) -> Any:
    """Render recent captures from session state (in-memory + DB seeded)."""
    captures = SESSION_STATE.current.captures if SESSION_STATE.current else []
    return templates.TemplateResponse(
        "dashboard/partials/captures.html",
        {"request": request, "captures": captures, "timezone": SESSION_STATE.timezone},
    )


@router.get("/dashboard/partials/solutions", response_class=HTMLResponse)
def solutions_partial(request: Request) -> Any:
    """Render solver view with target selector and per-frame solve status."""
    selected_target = request.query_params.get("target")
    with get_session() as session:
        # Fetch all targets, then filter out invalid ones (dates, directory names, etc.)
        target_rows = session.exec(
            select(CaptureLog.target, func.count().label("count"), func.max(CaptureLog.started_at).label("latest"))
            .where(CaptureLog.target.notin_(["Unknown", "LIGHT", "DARK", "BIAS", "FLAT", "SNAPSHOT", "Snapshot"]))
            .group_by(CaptureLog.target)
            .order_by(func.max(CaptureLog.started_at).desc())
            .limit(30)
        ).all()
        # Filter out targets that look like dates (YYYY-MM-DD pattern)
        import re
        date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
        targets = [{"name": row[0], "count": row[1], "latest": row[2]} for row in target_rows if not date_pattern.match(row[0])]
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
            "timezone": SESSION_STATE.timezone,
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
        {"request": request, "submissions": submissions, "timezone": SESSION_STATE.timezone},
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
def observatory_partial(request: Request, edit_site_id: int | None = None) -> Any:
    """Render site config snapshot."""
    from app.core.site_config import db_site_to_file_config
    import zoneinfo
    
    timezones = sorted(list(zoneinfo.available_timezones()))
    
    with get_session() as session:
        sites = session.exec(select(SiteConfig).order_by(SiteConfig.name)).all()
        active_site = next((s for s in sites if s.is_active), None)
        
        # If no active site, fallback to first or None
        if not active_site and sites:
            active_site = sites[0]
            
        # Determine which site to edit (if any)
        edit_site = None
        if edit_site_id:
            edit_site = session.get(SiteConfig, edit_site_id)
        elif not sites:
            # If no sites, show empty form to create one
            pass
        elif active_site:
            # Default to showing active site details if not editing another
            pass

        # Use active site for weather/horizon context
        context_site = active_site
        
        # Convert DB site to file config for WeatherService
        site_file_config = db_site_to_file_config(context_site) if context_site else None
        weather_service = WeatherService(session, site_config=site_file_config)
        weather_summary = weather_service.get_status()
    
    # Fetch bridge status to get ignore_weather flag
    from app.services.nina_client import NinaBridgeService
    bridge = NinaBridgeService()
    try:
        bridge_status = bridge.get_status()
        ignore_weather = bridge_status.get("ignore_weather", False)
    except Exception:
        ignore_weather = False

    weather_sources: list[dict[str, Any]] = []
    if context_site and context_site.weather_sensors:
        try:
            payload = json.loads(context_site.weather_sensors)
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
    formatted_weather = _format_weather_summary(weather_summary)
    
    return templates.TemplateResponse(
        "dashboard/partials/observatory.html",
        {
            "request": request,
            "sites": sites,
            "active_site": active_site,
            "edit_site": edit_site,
            "weather_sources": weather_sources,
            "weather_summary": formatted_weather,
            "ignore_weather": ignore_weather,
            "timezones": timezones,
        },
    )


@router.post("/dashboard/observatory/activate", response_class=HTMLResponse)
def observatory_activate(request: Request, site_id: int = Form(...)) -> Any:
    """Activate a site profile."""
    from app.api.site import activate_site
    with get_session() as session:
        activate_site(site_id, session)
    return observatory_partial(request)


@router.post("/dashboard/observatory/delete", response_class=HTMLResponse)
def observatory_delete(request: Request, site_id: int = Form(...)) -> Any:
    """Delete a site profile."""
    from app.models import SiteConfig
    with get_session() as session:
        site = session.get(SiteConfig, site_id)
        if site and not site.is_active:
            session.delete(site)
            session.commit()
    return observatory_partial(request)


@router.post("/dashboard/observatory/save", response_class=HTMLResponse)
async def observatory_save(
    request: Request,
    name: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...),
    altitude_m: float = Form(...),
    timezone: str = Form(...),
    bortle: int | None = Form(None),
    activate: bool = Form(False),
    site_id: int | None = Form(None),
) -> Any:
    """Save or update a site profile."""
    from app.api.site import upsert_site
    from app.models import SiteConfig
    
    with get_session() as session:
        if site_id:
            # Update existing
            existing = session.get(SiteConfig, site_id)
            if existing:
                existing.name = name
                existing.latitude = latitude
                existing.longitude = longitude
                existing.altitude_m = altitude_m
                existing.timezone = timezone
                existing.bortle = bortle
                
                if activate:
                     session.exec(update(SiteConfig).where(SiteConfig.id != site_id).values(is_active=False))
                     existing.is_active = True
                
                session.add(existing)
                session.commit()
                
                # Trigger horizon fetch
                from app.services.horizon import fetch_horizon_profile
                import asyncio
                asyncio.create_task(fetch_horizon_profile(existing.latitude, existing.longitude))
        else:
            # Create new
            payload = SiteConfig(
                name=name,
                latitude=latitude,
                longitude=longitude,
                altitude_m=altitude_m,
                timezone=timezone,
                bortle=bortle,
                telescope_design="Reflector", # Default
                telescope_aperture=0.0,
                telescope_detector="CCD",
                is_active=activate
            )
            
            # Use upsert logic
            await upsert_site(payload, session)

    return observatory_partial(request)


@router.post("/dashboard/observatory/{name}/refresh_horizon", response_class=HTMLResponse)
async def observatory_refresh_horizon(request: Request, name: str) -> Any:
    """Trigger horizon refresh and return updated partial."""
    from app.services.horizon import fetch_horizon_profile
    import logging
    
    logger = logging.getLogger(__name__)
    
    with get_session() as session:
        site = session.exec(select(SiteConfig).where(SiteConfig.name == name)).first()
        if site:
            logger.info("Refreshing horizon for site: %s", name)
            try:
                profile = await fetch_horizon_profile(site.latitude, site.longitude)
                site.horizon_mask_json = json.dumps(profile)
                session.add(site)
                session.commit()
            except Exception as exc:
                logger.error("Failed to refresh horizon: %s", exc, exc_info=True)
                pass
        else:
            logger.warning("Site not found for horizon refresh: %s", name)
                
    return observatory_partial(request)


def _format_weather_summary(summary: Any) -> dict[str, Any] | None:
    if not summary:
        return None
    
    # Map raw reasons to human-readable text
    reason_map = {
        "weather_precip_chance": "High Rain Risk",
        "weather_clouds": "Cloudy",
        "weather_wind": "High Wind",
        "weather_humidity": "High Humidity",
        "weather_rain": "Raining",
        "manual_override": "Manual Override",
        "dome_closed": "Dome Closed",
    }
    
    reasons = [reason_map.get(r, r) for r in summary.reasons]
    
    return {
        "temperature_c": round(summary.temperature_c, 1) if summary.temperature_c is not None else None,
        "wind_speed_mps": round(summary.wind_speed_mps, 1) if summary.wind_speed_mps is not None else None,
        "cloud_cover_pct": round(summary.cloud_cover_pct, 0) if summary.cloud_cover_pct is not None else None,
        "precipitation_probability_pct": round(summary.precipitation_probability_pct, 0) if summary.precipitation_probability_pct is not None else None,
        "is_safe": summary.is_safe,
        "reasons": reasons,
        "fetched_at": summary.fetched_at,
    }


@router.get("/dashboard/partials/equipment", response_class=HTMLResponse)
def equipment_partial(request: Request, edit_profile_id: int | None = None) -> Any:
    """Render the equipment management panel."""
    profiles = list_profiles()
    
    # Fetch site config for telescope details
    with get_session() as session:
        site_config = session.exec(select(SiteConfig).where(SiteConfig.name == settings.site_name)).first()
        
        # Determine which profile to load into the form
        form_profile = None
        if edit_profile_id:
            form_profile = session.get(EquipmentProfileRecord, edit_profile_id)
            if form_profile:
                # Parse JSON payload for template access
                # The template expects an object with attributes, but payload_json is a string
                # We need to attach the parsed payload to the object or return a dict
                import json
                payload = json.loads(form_profile.payload_json)
                # Create a simple object wrapper or dict merge
                # Let's just pass the payload dict, but we need 'name' from the record
                form_profile_data = payload
                form_profile_data['name'] = form_profile.name
                form_profile = form_profile_data
        else:
            # Default to active profile if available, or empty?
            # Current behavior was: value="{{ profile.camera.type if profile else 'mono' }}"
            # where 'profile' was the active one.
            # To maintain "Add New" behavior, we might want form_profile to be None if not editing.
            # But the user might want to see the active config.
            # Let's stick to: if edit_profile_id is passed, use that.
            # If not, use active profile (current behavior).
            active = get_active_equipment_profile()
            form_profile = active
        
    return templates.TemplateResponse(
        "dashboard/partials/equipment.html",
        {
            "request": request, 
            "profile": get_active_equipment_profile(), # Always show active at top
            "profiles": profiles,
            "site_config": site_config,
            "form_profile": form_profile # Profile to populate the form
        },
    )


def _load_targets(limit: int = 20) -> list[dict[str, Any]]:
    imaged_targets = set()
    if SESSION_STATE.current:
        for cap in SESSION_STATE.current.captures:
            t = cap.get("target")
            if t:
                imaged_targets.add(t)

    # Ensure the currently selected target is NOT filtered out, even if it has captures
    current_target = SESSION_STATE.selected_target
    if current_target and current_target in imaged_targets:
        imaged_targets.remove(current_target)

    with get_session() as session:
        stmt = (
            select(NeoObservability, NeoCandidate)
            .join(NeoCandidate, NeoCandidate.id == NeoObservability.candidate_id)
            .order_by(NeoObservability.score.desc(), NeoCandidate.updated_at.desc())
            .limit(limit)
        )
        rows = session.exec(stmt).all()
    total_rows = len(rows)
    
    from datetime import datetime
    now = datetime.utcnow()
    
    results = []
    for obs, cand in rows:
        # Filter out imaged targets
        if obs.trksub in imaged_targets:
            continue
            
        # Filter out targets that have already set (window end in past),
        # UNLESS it is the currently selected target (we might be finishing a run)
        if obs.window_end and obs.window_end <= now:
             if obs.trksub != SESSION_STATE.selected_target:
                 continue

        results.append({
            "trksub": obs.trksub,
            "score": obs.score,
            "is_observable": obs.is_observable,
            "duration_minutes": obs.duration_minutes,
            "window_start": obs.window_start,
            "window_end": obs.window_end,
            "max_altitude_deg": obs.max_altitude_deg,
            "min_moon_separation_deg": obs.min_moon_separation_deg,
            "vmag": cand.vmag,
            "candidate_id": cand.id,
        })
    if not results:
        logger.info(
            "Targets refresh: no candidates available (rows=%s, imaged_filtered=%s, time=%s)",
            total_rows,
            len(imaged_targets),
            now.isoformat(),
        )
    return results


@router.post("/dashboard/targets/refresh", response_class=HTMLResponse)
async def targets_refresh(
    request: Request,
    start_time: str | None = Form(None),
    end_time: str | None = Form(None)
) -> Any:
    """Refresh targets with optional custom time window."""
    from app.services.observability import ObservabilityService
    from datetime import datetime, timedelta, timezone, time as dt_time
    import zoneinfo
    
    if start_time and end_time:
        try:
            # Get local timezone
            tz_name = SESSION_STATE.timezone
            try:
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc

            now_utc = datetime.now(timezone.utc)
            now_local = now_utc.astimezone(tz)
            
            # Determine reference date (the "start" of the night)
            # If it's before noon, we assume the night started yesterday.
            if now_local.hour < 12:
                reference_date = now_local.date() - timedelta(days=1)
            else:
                reference_date = now_local.date()

            s_h, s_m = map(int, start_time.split(":"))
            e_h, e_m = map(int, end_time.split(":"))
            
            start_local = datetime.combine(reference_date, dt_time(s_h, s_m), tzinfo=tz)
            end_local = datetime.combine(reference_date, dt_time(e_h, e_m), tzinfo=tz)
            
            # If end is before start, it must be the next day
            if end_local <= start_local:
                end_local += timedelta(days=1)
            
            # Convert to naive UTC for ObservabilityService
            start_dt = start_local.astimezone(timezone.utc).replace(tzinfo=None)
            end_dt = end_local.astimezone(timezone.utc).replace(tzinfo=None)
            
            with get_session() as session:
                svc = ObservabilityService(session)
                svc.set_window(start_dt, end_dt)
                svc.refresh()
                
            # Persist to session state for UI stability
            SESSION_STATE.set_window(start_time, end_time)
            import logging
            logging.getLogger("uvicorn").info(f"Persisted window: {start_time} - {end_time} (Local: {start_local} -> {end_local})")
                
        except ValueError as e:
            import logging
            logging.getLogger("uvicorn").error(f"Window update failed: {e}")
            pass # Invalid format, ignore
            
    # Render main targets partial
    targets_response = _render_targets_partial(request, start_time=start_time, end_time=end_time)
    
    # Render OOB status partial
    status_response = _render_status_panel(request, oob=True)
    
    # Concatenate responses
    combined_content = targets_response.body + status_response.body
    
    import logging
    logging.getLogger("uvicorn").info("Targets refreshed. Returning OOB update for Overview.")
    
    return HTMLResponse(content=combined_content)


@router.post("/dashboard/targets/clear", response_class=HTMLResponse)
async def targets_clear(request: Request) -> Any:
    """Clear all NEOCP data and re-fetch fresh targets."""
    from sqlmodel import delete

    try:
        with get_session() as session:
            # Delete all NEOCP-related data in correct order (respecting foreign keys)
            logger.info("Clearing database: deleting NEOCP-related records")

            # Delete dependent records first
            session.exec(delete(Measurement))
            session.exec(delete(AstrometricSolution))
            session.exec(delete(CandidateAssociation))
            session.exec(delete(SubmissionLog))
            session.exec(delete(NeoObservability))
            session.exec(delete(NeoEphemeris))
            session.exec(delete(NeoCandidate))

            session.commit()
            logger.info("Database cleared successfully")

        # Re-fetch targets using the appropriate service
        # Check if we're in synthetic mode or using real NEOCP data
        logger.info("Re-fetching targets")

        if settings.neocp_use_local_sample:
            # Use synthetic targets for testing
            from app.services.synthetic_targets import SyntheticTargetService
            service = SyntheticTargetService()
            service.seed_targets()
            logger.info("Targets seeded successfully")
        else:
            # Fetch from real NEOCP feed
            from app.services.neocp_fetcher import NeoCPFetcherService
            service = NeoCPFetcherService()
            service.run_cycle()
            logger.info("NEOCP targets fetched successfully")

        # Render targets partial with success message
        status_banner = {
            "level": "good",
            "message": "Database cleared and targets re-fetched successfully"
        }

    except Exception as e:
        logger.error(f"Failed to clear and reseed database: {e}")
        status_banner = {
            "level": "error",
            "message": f"Failed to clear and reseed: {str(e)}"
        }

    # Render main targets partial
    targets_response = _render_targets_partial(request)

    # Render OOB status partial with banner
    status_response = _render_status_panel(request, status_banner=status_banner, oob=True)

    # Concatenate responses
    combined_content = targets_response.body + status_response.body

    return HTMLResponse(content=combined_content)


@router.get("/dashboard/partials/targets", response_class=HTMLResponse)
def targets_partial(request: Request) -> Any:
    """Render top observability-ranked targets."""
    return _render_targets_partial(request)


def to_local_filter(value: Any, tz_name: str = "UTC") -> str:
    if not value:
        return ""
    
    from datetime import datetime, timezone
    import zoneinfo
    
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
            
    if not value.tzinfo:
        value = value.replace(tzinfo=timezone.utc)
        
    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = timezone.utc
        
    local_dt = value.astimezone(tz)
    return local_dt.strftime("%H:%M")

templates.env.filters["to_local"] = to_local_filter


def _render_targets_partial(
    request: Request,
    targets: list[dict[str, Any]] | None = None,
    error: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
) -> HTMLResponse:
    if targets is None:
        targets = _load_targets()
    
    # Default times if not provided (e.g. current night window)
    if not start_time:
        start_time = SESSION_STATE.window_start or "18:00"
    if not end_time:
        end_time = SESSION_STATE.window_end or "06:00"

    import logging
    logging.getLogger("uvicorn").info(f"Rendering targets with window: {start_time} - {end_time} (Session: {SESSION_STATE.window_start}-{SESSION_STATE.window_end})")

    active_preset = None
    active_target_data = None
    
    # Determine which target is "active" (manual selection or top auto pick)
    if SESSION_STATE.target_mode == "manual" and SESSION_STATE.selected_target:
        active_target_data = next((t for t in targets if t["trksub"] == SESSION_STATE.selected_target), None)
    elif targets:
        # Pick the first target that is actually observable
        active_target_data = next((t for t in targets if t["is_observable"]), None)
        
    if active_target_data:
        from app.services.presets import select_preset
        from app.services.equipment import get_active_equipment_profile
        from app.services.motion import estimate_motion_rate_arcsec_per_min

        profile = get_active_equipment_profile()
        score = active_target_data.get("score")
        urgency = max(0.0, min(1.0, score / 100.0)) if score is not None else None
        motion_rate = None
        if active_target_data.get("candidate_id"):
            with get_session() as motion_session:
                motion_rate = estimate_motion_rate_arcsec_per_min(
                    motion_session,
                    active_target_data.get("candidate_id"),
                )

        active_preset = select_preset(
            vmag=active_target_data.get("vmag"),
            profile=profile,
            urgency=urgency,
            motion_rate_arcsec_min=motion_rate,
            pixel_scale_arcsec_per_pixel=settings.astrometry_pixel_scale_arcsec,
        )

    return templates.TemplateResponse(
        "dashboard/partials/targets.html",
        {
            "request": request,
            "targets": targets,
            "target_mode": SESSION_STATE.target_mode,
            "selected_target": SESSION_STATE.selected_target,
            "active_preset": active_preset,
            "active_target": active_target_data,
            "error": error,
            "start_time": start_time,
            "end_time": end_time,
            "timezone": SESSION_STATE.timezone,
        },
    )


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
        {"request": request, "submissions": submissions, "timezone": SESSION_STATE.timezone},
    )


@router.get("/dashboard/partials/session_status", response_class=HTMLResponse)
def session_status_partial_panel(request: Request) -> Any:
    """Render session status for the exposures tab."""
    session_info = {"active": False}
    if SESSION_STATE.current:
        session_info = SESSION_STATE.current.to_dict()
        session_info["active"] = True
    return templates.TemplateResponse(
        "dashboard/partials/session_status.html",
        {"request": request, "session": session_info, "timezone": SESSION_STATE.timezone},
    )


@router.get("/dashboard/partials/association", response_class=HTMLResponse)
def association_partial(request: Request, error: str | None = None) -> Any:
    """Association workflow view: select target and annotate centroids."""
    selected_target = request.query_params.get("target")
    
    with get_session() as session:
        # Load targets with recent captures
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
        associations = {}
        predicted = {}
        
        if selected_target:
            # Load captures for the selected target
            capture_rows = session.exec(
                select(CaptureLog).where(CaptureLog.target == selected_target).order_by(CaptureLog.started_at.desc())
            ).all()
            captures = list(capture_rows)
            capture_ids = [c.id for c in captures if c.id]
            
            if capture_ids:
                # Load existing manual associations
                assoc_rows = session.exec(
                    select(CandidateAssociation).where(CandidateAssociation.capture_id.in_(capture_ids))
                ).all()
                associations = {row.capture_id: row for row in assoc_rows}
                
                # Load predictions (ephemeris) - simplified logic: find nearest ephemeris
                # Ideally we'd interpolate, but for now we'll just look for a close match if we have the candidate
                candidate = session.exec(select(NeoCandidate).where(NeoCandidate.trksub == selected_target)).first()
                if candidate:
                    # Fetch ephemeris for the time range of captures
                    min_time = min(c.started_at for c in captures)
                    max_time = max(c.started_at for c in captures)
                    # Pad the range slightly
                    eph_rows = session.exec(
                        select(NeoEphemeris)
                        .where(
                            NeoEphemeris.candidate_id == candidate.id,
                            NeoEphemeris.epoch >= min_time,
                            NeoEphemeris.epoch <= max_time
                        )
                    ).all()
                    
                    # Map each capture to the nearest ephemeris point (simple approach)
                    for cap in captures:
                        best_eph = None
                        min_diff = float("inf")
                        for eph in eph_rows:
                            diff = abs((eph.epoch - cap.started_at).total_seconds())
                            if diff < min_diff:
                                min_diff = diff
                                best_eph = eph
                        
                        if best_eph and min_diff < 300: # Within 5 minutes
                             predicted[cap.path] = {"ra_deg": best_eph.ra_deg, "dec_deg": best_eph.dec_deg}
                             # Also map by ID for template convenience if needed, but template uses path currently
                             # We might need to adjust template to use ID or keep using path as key
                             
    # Transform associations to map by path for the template compatibility
    associations_by_path = {}
    for cap in captures:
        if cap.id in associations:
            associations_by_path[cap.path] = associations[cap.id]

    return templates.TemplateResponse(
        "dashboard/partials/association.html",
        {
            "request": request,
            "targets": targets,
            "selected_target": selected_target,
            "captures": captures,
            "associations": associations_by_path,
            "predicted": predicted,
            "error": error,
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
            
            with get_session() as session:
                # Find the capture log entry
                capture = session.exec(select(CaptureLog).where(CaptureLog.path == path)).first()
                if capture and capture.id:
                    # Check for existing association
                    existing = session.exec(
                        select(CandidateAssociation).where(CandidateAssociation.capture_id == capture.id)
                    ).first()
                    
                    if existing:
                        existing.ra_deg = ra_f
                        existing.dec_deg = dec_f
                        session.add(existing)
                    else:
                        new_assoc = CandidateAssociation(
                            capture_id=capture.id,
                            ra_deg=ra_f,
                            dec_deg=dec_f
                        )
                        session.add(new_assoc)
                    
                    # Sync to Measurement table for reporting
                    # We assume a measurement exists for this capture (created by solver)
                    # If not, we should probably create one, but typically solver creates it.
                    measurement = session.exec(
                        select(Measurement).where(Measurement.capture_id == capture.id)
                    ).first()
                    
                    if measurement:
                        measurement.ra_deg = ra_f
                        measurement.dec_deg = dec_f
                        measurement.reviewed = True
                        session.add(measurement)
                    else:
                        # Create new measurement if missing (self-healing)
                        from app.models import Measurement
                        measurement = Measurement(
                            capture_id=capture.id,
                            target=capture.target or "unknown",
                            obs_time=capture.started_at,
                            ra_deg=ra_f,
                            dec_deg=dec_f,
                            reviewed=True,
                            # Defaults
                            ra_uncert_arcsec=None,
                            dec_uncert_arcsec=None,
                            magnitude=None,
                            band="R", # Default
                            station_code=settings.station_code
                        )
                        session.add(measurement)
                    
                    session.commit()
                else:
                    error = "Capture log not found for this file."
                    
        except Exception:
            error = "Invalid RA/Dec values."
            
    # Re-render association partial
    return association_partial(request, error=error)


@router.post("/dashboard/analysis/project")
async def analysis_project(request: Request) -> Any:
    """Project RA/Dec to pixel coordinates for a given capture."""
    form = await request.form()
    path = form.get("path")
    ra = form.get("ra_deg")
    dec = form.get("dec_deg")
    
    if not path or not ra or not dec:
        return JSONResponse({"error": "Missing parameters"}, status_code=400)
        
    try:
        ra_f = float(ra)
        dec_f = float(dec)
        
        # Load WCS
        from astropy.wcs import WCS
        from astropy.io import fits
        import warnings
        
        with fits.open(path) as hdul:
            header = hdul[0].header
            
        wcs_path = Path(path).with_suffix(".wcs")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if wcs_path.exists():
                wcs = WCS(str(wcs_path))
            else:
                wcs = WCS(header)
                
        # Convert to pixels
        x, y = wcs.all_world2pix(ra_f, dec_f, 1) # 1-based origin for FITS, but we might need 0-based for canvas?
        # Astropy returns 0-based if origin=0. Let's use 0-based for canvas.
        x, y = wcs.all_world2pix(ra_f, dec_f, 0)
        
        return JSONResponse({"x": float(x), "y": float(y)})
        
    except Exception as e:
        logging.error(f"Projection error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/dashboard/analysis/resolve_click")
async def analysis_resolve_click(request: Request) -> Any:
    """Resolve a click on an image to a precise centroid and RA/Dec."""
    try:
        data = await request.json()
    except json.JSONDecodeError:
        return {"error": "Invalid JSON"}
        
    path = data.get("path")
    click_x = data.get("x")
    click_y = data.get("y")
    polygon = data.get("polygon")
    
    if not path or (polygon is None and (click_x is None or click_y is None)):
        return {"error": "Missing path, x/y, or polygon"}
        
    with get_session() as session:
        capture = session.exec(select(CaptureLog).where(CaptureLog.path == path)).first()
        if not capture:
            return {"error": "Capture not found"}
            
        from app.services.analysis import AnalysisService
        svc = AnalysisService(session)
        
        x_val = float(click_x) if click_x is not None else None
        y_val = float(click_y) if click_y is not None else None
        
        result = svc.resolve_click(capture, x_val, y_val, polygon=polygon)
        
        if result:
            return result
        return {"error": "Could not resolve source at this location"}


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
        SESSION_STATE.end(reason=exc.message)
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
            session.exec(CandidateAssociation.__table__.delete().where(CandidateAssociation.capture_id.in_(capture_ids)))
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


@router.get("/dashboard/partials/review_modal", response_class=HTMLResponse)
def review_modal_partial(request: Request, path: str) -> Any:
    """Render the interactive review modal."""
    preview = None
    error = None
    meta = {"path": path}
    
    # Navigation logic
    prev_capture = None
    next_capture = None
    current_index = 0
    total_count = 0
    existing_association = None
    
    with get_session() as session:
        # Find current capture
        current = session.exec(select(CaptureLog).where(CaptureLog.path == path)).first()
        if current:
            if current.target:
                meta["target"] = current.target
                # Find siblings
                siblings = session.exec(
                    select(CaptureLog)
                    .where(CaptureLog.target == current.target)
                    .order_by(CaptureLog.started_at)
                ).all()
                
                total_count = len(siblings)
                try:
                    # Find index of current capture
                    # Use ID if available, else path
                    if current.id:
                        current_index = next(i for i, c in enumerate(siblings) if c.id == current.id)
                    else:
                        current_index = next(i for i, c in enumerate(siblings) if c.path == path)
                        
                    if current_index > 0:
                        prev_capture = siblings[current_index - 1]
                    if current_index < total_count - 1:
                        next_capture = siblings[current_index + 1]
                except StopIteration:
                    pass
            
            # Check for existing association
            if current.id:
                existing_association = session.exec(
                    select(CandidateAssociation).where(CandidateAssociation.capture_id == current.id)
                ).first()

    try:
        preview = _generate_fits_preview(path)
    except Exception as exc:
        error = f"Unable to render FITS preview: {exc}"
        
    return templates.TemplateResponse(
        "dashboard/partials/review_modal.html",
        {
            "request": request,
            "preview": preview,
            "error": error,
            "meta": meta,
            "navigation": {
                "prev": prev_capture.path if prev_capture else None,
                "next": next_capture.path if next_capture else None,
                "current": current_index + 1,
                "total": total_count
            },
            "association": existing_association,
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


@router.post("/dashboard/equipment/delete", response_class=HTMLResponse)
def equipment_delete(request: Request, profile_id: int = Form(...)) -> Any:
    """Delete an equipment profile."""
    from app.services.equipment import delete_profile
    with get_session() as session:
        delete_profile(session, profile_id)
    return equipment_partial(request)


@router.post("/dashboard/equipment/save", response_class=HTMLResponse)
async def equipment_save(request: Request) -> Any:
    """Create/update an equipment profile from the dashboard form."""
    # Parse form data
    form = await request.form()
    name = form.get("name")
    
    # Camera
    camera_type = form.get("camera_type", "mono")
    camera_filters_str = form.get("camera_filters", "")
    camera_filters = [f.strip() for f in camera_filters_str.split(",") if f.strip()]
    
    # Mount
    mount_parking = form.get("mount_supports_parking") == "on"
    
    # Telescope
    telescope_design = form.get("telescope_design", "Reflector")
    telescope_aperture = float(form.get("telescope_aperture", 0.0)) / 1000.0 # mm to m
    telescope_detector = form.get("telescope_detector", "CCD")
    
    from app.services.equipment import (
        EquipmentProfileSpec, 
        CameraCapabilities, 
        MountCapabilities,
        TelescopeCapabilities,
        save_profile
    )
    
    payload = EquipmentProfileSpec(
        camera=CameraCapabilities(
            type=camera_type,
            filters=camera_filters,
            max_binning=2 # Default
        ),
        mount=MountCapabilities(supports_parking=mount_parking),
        telescope=TelescopeCapabilities(
            design=telescope_design,
            aperture=telescope_aperture,
            detector=telescope_detector
        )
    )
    
    # Save
    activate = form.get("activate") == "on"
    save_profile(name, payload, activate=activate)
    
    return equipment_partial(request)



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


@router.get("/dashboard/partials/reports_tab", response_class=HTMLResponse)
async def reports_tab(request: Request) -> Any:
    """Render the reports tab content."""
    with get_session() as session:
        # Fetch pending measurements (reviewed=True)
        measurements = session.exec(
            select(Measurement).where(Measurement.reviewed == True).order_by(Measurement.target, Measurement.obs_time)
        ).all()
        
        # Group by target
        grouped = {}
        for m in measurements:
            if m.target not in grouped:
                grouped[m.target] = []
            grouped[m.target].append(m)
            
        pending_targets = []
        for target, ms in grouped.items():
            if not ms:
                continue
            # Calculate span
            times = [m.obs_time for m in ms]
            span_str = f"{min(times).strftime('%H:%M')} - {max(times).strftime('%H:%M')}"
            pending_targets.append({
                "name": target,
                "count": len(ms),
                "span": span_str
            })
            
        # Fetch submission history
        submissions = session.exec(
            select(SubmissionLog).order_by(SubmissionLog.created_at.desc()).limit(10)
        ).all()
        
    return templates.TemplateResponse(
        "dashboard/partials/reports_tab.html",
        {
            "request": request,
            "pending_targets": pending_targets,
            "submissions": submissions
        }
    )


@router.get("/dashboard/reports/preview", response_class=HTMLResponse)
async def reports_preview(request: Request, target: str) -> Any:
    """Render report preview modal for a specific target."""
    with get_session() as session:
        # Fetch measurements for this target
        measurements = session.exec(
            select(Measurement)
            .where(Measurement.target == target)
            .where(Measurement.reviewed == True)
            .order_by(Measurement.obs_time)
        ).all()
        
        if not measurements:
            return "<div>No measurements found for target.</div>"
            
        from app.services.reporting import ReportService
        svc = ReportService(session)
        
        # Generate both formats for preview
        ades_content = svc.generate_ades(measurements)
        mpc_content = svc.generate_mpc80(measurements)
        
        # Get IDs for submission
        ids = [m.id for m in measurements if m.id]
        ids_json = json.dumps(ids)
        
    return templates.TemplateResponse(
        "dashboard/partials/report_preview.html",
        {
            "request": request,
            "target": target,
            "ades_content": ades_content,
            "mpc_content": mpc_content,
            "ids_json": ids_json,
            "count": len(measurements)
        }
    )


@router.post("/dashboard/reports/submit", response_class=HTMLResponse)
async def reports_submit(request: Request) -> Any:
    """Handle report submission."""
    form = await request.form()
    ids_json = form.get("ids_json")
    format_type = form.get("format") or "ades"
    
    if not ids_json:
        return "<div>Error: No measurements selected.</div>"
        
    try:
        ids = json.loads(ids_json)
    except json.JSONDecodeError:
        return "<div>Error: Invalid measurement IDs.</div>"
        
    with get_session() as session:
        measurements = session.exec(
            select(Measurement).where(Measurement.id.in_(ids))
        ).all()
        
        if not measurements:
            return "<div>Error: Measurements not found.</div>"
            
        from app.services.reporting import ReportService
        svc = ReportService(session)
        
        # Generate payload based on selected format
        if format_type == "ades":
            payload = svc.generate_ades(measurements)
        else:
            payload = svc.generate_mpc80(measurements)
            
        # Submit
        # TODO: Real submission logic (email/API)
        # For now, just log it
        svc.submit_report(payload, channel="mock", measurement_ids=ids)
        
        # Mark as submitted? 
        # We don't have a 'submitted' flag on Measurement yet, but we have the log.
        # Ideally we'd update Measurement status here.
        
    # Return success message or refresh reports tab
    return reports_tab(request)


__all__ = ["router"]
