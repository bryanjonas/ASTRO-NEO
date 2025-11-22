"""Lightweight automation helpers for one-shot target runs.

This module chains the bridge actions (connect → slew → optional focus → start
sequence → optional park) with basic weather/override checks. It is synchronous
for the initial actions and spawns a background thread if auto-park is
requested.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.services.imaging import build_fits_path
from app.services.nina_bridge import NinaBridgeService
from app.services.session import SESSION_STATE
from app.services.equipment import get_active_equipment_profile
from app.services.presets import select_preset
from app.services.weather import WeatherService
from app.services.task_queue import TASK_QUEUE, Task
from app.db.session import get_session

logger = logging.getLogger(__name__)


@dataclass
class AutomationPlan:
    target: str
    ra_deg: float
    dec_deg: float
    filter: str
    binning: int
    exposure_seconds: float
    count: int
    focus_position: int | None = None
    park_after: bool = False


class AutomationService:
    """Coordinate bridge calls into a simple one-shot target run."""

    def __init__(self, bridge: NinaBridgeService | None = None) -> None:
        self.bridge = bridge or NinaBridgeService()

    def _ensure_weather_safe(self) -> None:
        with get_session() as session:
            summary = WeatherService(session).get_status()
        if summary and not summary.is_safe:
            raise HTTPException(status_code=423, detail={"reason": "weather_blocked", "factors": summary.reasons})

    def build_plan(
        self,
        target: str,
        ra_deg: float,
        dec_deg: float,
        vmag: float | None = None,
        urgency: float | None = None,
        focus_position: int | None = None,
        park_after: bool = False,
        override_filter: str | None = None,
        override_binning: int | None = None,
        override_exposure_seconds: float | None = None,
        override_count: int | None = None,
    ) -> AutomationPlan:
        """Construct an AutomationPlan, using presets when overrides are not provided."""

        profile = None
        try:
            profile = get_active_equipment_profile()
        except Exception:  # pragma: no cover - best-effort lookup
            pass

        preset = select_preset(vmag, profile=profile, urgency=urgency)
        filter_name = override_filter or preset.filter
        binning = override_binning or preset.binning
        exposure_seconds = override_exposure_seconds or preset.exposure_seconds
        count = override_count or preset.count

        return AutomationPlan(
            target=target,
            ra_deg=ra_deg,
            dec_deg=dec_deg,
            filter=filter_name,
            binning=binning,
            exposure_seconds=exposure_seconds,
            count=count,
            focus_position=focus_position,
            park_after=park_after,
        )

    def run_plan(self, plan: AutomationPlan) -> dict[str, Any]:
        """Execute the automation sequence up to starting exposures."""

        self._ensure_weather_safe()
        started_at = datetime.utcnow()
        logger.info("Automation: connecting telescope for %s", plan.target)
        self.bridge.connect_telescope(True)
        # Queue retryable tasks so failures raise dashboard alerts
        if plan.focus_position is not None:
            TASK_QUEUE.submit(Task(name="focus_move", func=lambda: self.bridge.focuser_move(plan.focus_position)))

        TASK_QUEUE.submit(
            Task(
                name="telescope_slew",
                func=lambda: self.bridge.slew(plan.ra_deg, plan.dec_deg),
            )
        )

        sequence_payload = {
            "name": plan.target,
            "count": plan.count,
            "filter": plan.filter,
            "binning": plan.binning,
            "exposure_seconds": plan.exposure_seconds,
            "target": plan.target,
            "tracking_mode": "sidereal",
        }
        TASK_QUEUE.submit(
            Task(
                name="sequence_start",
                func=lambda: self.bridge.start_sequence(sequence_payload),
            )
        )

        expected_paths: list[dict[str, Any]] = []
        for idx in range(1, plan.count + 1):
            path = build_fits_path(plan.target, started_at, sequence_name=plan.target, index=idx)
            expected_paths.append({"index": idx, "path": str(path)})
        SESSION_STATE.add_captures(
            [
                {
                    "kind": "sequence",
                    "target": plan.target,
                    "sequence": plan.target,
                    "index": entry["index"],
                    "started_at": started_at.isoformat(),
                    "path": entry["path"],
                }
                for entry in expected_paths
            ]
        )

        if plan.park_after:
            total_duration = max(plan.exposure_seconds * plan.count + 30.0, 0.0)
            threading.Thread(target=self._park_after, args=(total_duration,), daemon=True).start()

        return {
            "target": plan.target,
            "started_at": started_at.isoformat(),
            "expected_paths": expected_paths,
            "park_after": plan.park_after,
        }

    def _park_after(self, delay_seconds: float) -> None:
        logger.info("Automation: will park after %.1fs", delay_seconds)
        time.sleep(delay_seconds)
        try:
            self.bridge.park_telescope(True)
            logger.info("Automation: telescope parked")
        except Exception as exc:  # pragma: no cover - keep background thread silent
            logger.warning("Automation: failed to park telescope: %s", exc)


__all__ = ["AutomationPlan", "AutomationService"]
