"""Monitoring endpoint for guiding/cloud/IQ flags."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services.monitoring import MonitoringService

router = APIRouter(prefix="/monitor", tags=["monitor"])

service = MonitoringService()


@router.post("/ingest")
def ingest(metrics: dict[str, Any]) -> dict[str, Any]:
    target = metrics.get("target")
    return service.evaluate(metrics, target=target)


@router.get("/reschedule")
def reschedule_queue() -> dict[str, Any]:
    return {"reschedules": service.reschedule_queue()}

