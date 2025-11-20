"""Root API routers."""

from fastapi import APIRouter

health_router = APIRouter(tags=["system"])


@health_router.get("/health", summary="Service health probe")
async def healthcheck() -> dict[str, str]:
    """Return a simple heartbeat for orchestration layers."""

    return {"status": "ok"}
