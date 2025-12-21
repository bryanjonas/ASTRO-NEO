"""API router definitions."""

from fastapi import APIRouter

from .associations import router as associations_router
from .bridge import router as bridge_router
# Temporarily disabled - requires SESSION_STATE refactoring
# from .dashboard import router as dashboard_router
from .astrometry import router as astrometry_router
from .monitor import router as monitor_router
from .observability import router as observability_router
from .equipment_profiles import router as equipment_router
from .routes import health_router
# Temporarily disabled - requires SESSION_STATE refactoring
# from .session import router as session_router
from .site import router as site_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(site_router)
api_router.include_router(observability_router)
api_router.include_router(bridge_router)
api_router.include_router(equipment_router)
# api_router.include_router(session_router)
# api_router.include_router(dashboard_router)
api_router.include_router(astrometry_router)
api_router.include_router(associations_router)
api_router.include_router(monitor_router)

__all__ = ["api_router"]
