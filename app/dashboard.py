"""Dashboard page routes and partials."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.session import dashboard_status as session_dashboard_status
from app.api.retention import retention_status
from app.services.session import SESSION_STATE

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


@router.get("/dashboard/partials/status", response_class=HTMLResponse)
def dashboard_status_partial(request: Request) -> Any:
    """HTMX-friendly status bundle."""
    bundle = session_dashboard_status()
    retention = retention_status()
    return templates.TemplateResponse(
        "dashboard/partials/status.html",
        {
            "request": request,
            "bundle": bundle,
            "retention": retention,
        },
    )


@router.get("/dashboard/partials/captures", response_class=HTMLResponse)
def captures_partial(request: Request) -> Any:
    """Render recent captures from session state (in-memory + DB seeded)."""
    captures = SESSION_STATE.current.captures if SESSION_STATE.current else []
    return templates.TemplateResponse(
        "dashboard/partials/captures.html",
        {"request": request, "captures": captures},
    )


__all__ = ["router"]
