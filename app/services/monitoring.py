"""Monitoring helpers for guiding/cloud/IQ thresholds."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.services.notifications import NOTIFICATIONS


@dataclass
class RescheduleRequest:
    target: str | None
    flags: list[str] = field(default_factory=list)


class MonitoringService:
    """Evaluate guiding/cloud/IQ metrics and flag reschedules."""

    def __init__(self) -> None:
        self.guiding_max_rms = settings.guiding_max_rms_arcsec
        self.iq_max_fwhm = settings.iq_max_fwhm_arcsec
        self.cloud_max_pct = settings.weather_max_cloud_cover_pct
        self._reschedules: list[RescheduleRequest] = []

    def evaluate(self, metrics: dict[str, Any], target: str | None = None) -> dict[str, Any]:
        flags: list[str] = []
        rms = _safe_float(metrics.get("guiding_rms_arcsec"))
        fwhm = _safe_float(metrics.get("fwhm_arcsec"))
        cloud = _safe_float(metrics.get("cloud_cover_pct"))

        if rms is not None and rms > self.guiding_max_rms:
            flags.append("high_guiding_rms")
        if fwhm is not None and fwhm > self.iq_max_fwhm:
            flags.append("poor_iq")
        if cloud is not None and cloud > self.cloud_max_pct:
            flags.append("cloudy")

        reschedule = bool(flags)
        if reschedule:
            req = RescheduleRequest(target=target, flags=flags)
            self._reschedules.append(req)
            NOTIFICATIONS.add(
                "warn",
                f"Reschedule requested for {target or 'current target'}",
                {"flags": flags, "metrics": metrics},
            )
        return {"reschedule": reschedule, "flags": flags}

    def reschedule_queue(self) -> list[dict[str, Any]]:
        return [{"target": r.target, "flags": r.flags} for r in self._reschedules]


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["MonitoringService", "RescheduleRequest"]
