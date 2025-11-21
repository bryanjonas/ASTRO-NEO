"""API router definitions."""

from fastapi import APIRouter

from .bridge import router as bridge_router
from .dashboard import router as dashboard_router
from .observability import router as observability_router
from .routes import health_router
from .retention import router as retention_router
from .session import router as session_router
from .site import router as site_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(site_router, prefix="/site")
api_router.include_router(observability_router)
api_router.include_router(bridge_router)
api_router.include_router(session_router)
api_router.include_router(retention_router)
api_router.include_router(dashboard_router)

__all__ = ["api_router"]
