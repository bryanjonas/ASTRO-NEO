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
        # Fetch latest WhatsUp targets and cache Horizons positions in background.
        import threading

        def _startup_refresh() -> None:
            try:
                from .db.session import get_session
                with get_session() as session:
                    service = WhatsUpService(session=session)
                    service.ensure_targets(force=True)
                    top = service.get_ranked_targets(limit=settings.whatsup_max_objects)
                    service.ensure_horizons_cache(top[:10])
            except Exception as exc:
                logger.warning("WhatsUp fetch on startup failed: %s", exc)

        threading.Thread(target=_startup_refresh, daemon=True).start()

    return app


app = create_app()
