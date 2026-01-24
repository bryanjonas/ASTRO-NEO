"""ASTRO-NEO FastAPI application package."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .core.config import settings
from .core.logging_config import setup_logging
from .core.site_config import bootstrap_site_config
from .db.session import init_db
from .dashboard_router import router as dashboard_router
from .services.captures import prune_missing_captures
from .services.whatsup import WhatsUpService


import logging

def create_app() -> FastAPI:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("Initializing ASTRO-NEO API")

    app = FastAPI(title=settings.app_name, version=settings.app_version)
    app.include_router(api_router, prefix=settings.api_prefix)
    app.include_router(dashboard_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

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
            from datetime import datetime

            from .models import NeoCandidate, NeoEphemeris, ObservingSession

            with get_session() as session:
                target_ids = session.exec(
                    select(NeoCandidate.id).where(
                        NeoCandidate.status.in_(["WHATSUP", "WHATSUP_NO_HORIZONS"])
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
                    select(ObservingSession).where(ObservingSession.status == "active")
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

        logger.info("Startup complete; waiting for manual WhatsUp refresh.")

    return app


app = create_app()
