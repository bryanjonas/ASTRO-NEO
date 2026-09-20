"""ASTRO-NEO FastAPI application package."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .core.config import settings
from .core.logging_config import setup_logging
from .core.site_config import bootstrap_site_config
from .db.session import init_db
from .dashboard_router import router as dashboard_router
from .services.captures import prune_missing_captures
from .services.whatsup import WhatsUpService, start_periodic_refresh_thread


import logging

def create_app() -> FastAPI:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Initializing ASTRO-NEO API")

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(dashboard_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # New SPA frontend (React + Vite, built to app/frontend_dist -- see
    # frontend/ for source). Mounted at /app, with a catch-all fallback so
    # client-side routes (e.g. /app/hardware) work on direct load/refresh,
    # not just client-side navigation from the SPA's own root.
    frontend_dist = Path("app/frontend_dist")
    if frontend_dist.exists():
        app.mount(
            "/app/assets",
            StaticFiles(directory=str(frontend_dist / "assets")),
            name="frontend-assets",
        )

        @app.get("/app", include_in_schema=False)
        @app.get("/app/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str = "") -> FileResponse:
            # Serve a real file if the path matches one on disk (e.g. the
            # root-level favicon.svg, which Vite doesn't put under
            # /assets); otherwise fall back to index.html so client-side
            # routes (e.g. /app/hardware) work on direct load/refresh too.
            candidate = frontend_dist / full_path
            if full_path and candidate.is_file():
                return FileResponse(str(candidate))
            return FileResponse(str(frontend_dist / "index.html"))

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        """Provide a friendly landing response for the bare hostname."""

        return {
            "message": (
                "ASTRO-NEO API is online. Try GET "
                f"{settings.api_prefix}/health for a health check."
            )
        }

    @app.on_event("startup")
    def _bootstrap_site_config() -> None:
        bootstrap_site_config()
        init_db()
        prune_missing_captures()
        try:
            from .db.session import get_session
            from sqlmodel import select, delete
            from datetime import datetime, timedelta

            from .models import NeoCandidate, NeoEphemeris, ObservingSession

            with get_session() as session:
                # Only clear WHATSUP targets that are actually stale -- not
                # unconditionally on every boot. A restart minutes after a
                # real refresh (e.g. a routine redeploy) shouldn't throw away
                # still-valid data and force an immediate re-scrape; the
                # periodic refresh thread (started below) already re-checks
                # freshness on its own schedule regardless.
                stale_cutoff = datetime.utcnow() - timedelta(
                    minutes=settings.whatsup_refresh_minutes
                )
                target_ids = session.exec(
                    select(NeoCandidate.id)
                    .where(NeoCandidate.status.in_(["WHATSUP", "WHATSUP_NO_HORIZONS"]))
                    .where(
                        (NeoCandidate.updated_at == None)  # noqa: E711
                        | (NeoCandidate.updated_at < stale_cutoff)
                    )
                ).all()
                if target_ids:
                    session.exec(
                        delete(NeoEphemeris).where(NeoEphemeris.candidate_id.in_(target_ids))
                    )
                    session.exec(
                        delete(NeoCandidate).where(NeoCandidate.id.in_(target_ids))
                    )
                    session.commit()
                active_sessions = session.exec(
                    select(ObservingSession).where(
                        ObservingSession.status.in_(["active", "stopping"])
                    )
                ).all()
                if active_sessions:
                    now = datetime.utcnow()
                    for active in active_sessions:
                        active.status = "stopped"
                        active.end_time = now
                    session.commit()
                    logger.info("Stopped %d active session(s) on startup", len(active_sessions))
        except Exception as exc:
            logger.warning("Failed to clear WhatsUp targets on startup: %s", exc)

        start_periodic_refresh_thread()
        logger.info("Startup complete; WhatsUp targets will refresh automatically.")

    return app


app = create_app()
