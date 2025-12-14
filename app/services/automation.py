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

from app.services.nina_client import NinaBridgeService
from app.services.session import SESSION_STATE
from app.services.equipment import get_active_equipment_profile
from app.services.presets import select_preset, ExposurePreset
from app.services.weather import WeatherService
from app.services.task_queue import TASK_QUEUE, Task
from app.db.session import get_session
from app.services.prediction import EphemerisPredictionService
from app.services.capture_loop import CaptureLoopResult, CaptureTargetDescriptor, run_capture_loop

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


@dataclass
class SequentialTargetPlan:
    """Plan describing the sequential single-target workflow."""
    name: str
    targets: list[dict[str, Any]]
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
        self._ensure_devices_ready()
        started_at = datetime.utcnow()
        guiding_started = False
        try:
            self.bridge.start_guiding()
            guiding_started = True
            SESSION_STATE.log_event("Guiding started for sequential sequence", "info")
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning("Automation: failed to start guiding: %s", exc)
            SESSION_STATE.log_event("Guiding could not be enabled for sequence", "warn")
        SESSION_STATE.log_event(f"Automation: Starting run for {plan.target}", "info")
        # Queue retryable tasks so failures raise dashboard alerts
        if plan.focus_position is not None:
            TASK_QUEUE.submit(
                Task(
                    name="focus_move",
                    func=lambda: (SESSION_STATE.log_event(f"Automation: Focusing to {plan.focus_position}", "info"), self.bridge.focuser_move(plan.focus_position))
                )
            )

        TASK_QUEUE.submit(
            Task(
                name="telescope_slew",
                func=lambda: (SESSION_STATE.log_event(f"Automation: Slewing to {plan.target}", "info"), self.bridge.slew(plan.ra_deg, plan.dec_deg)),
            )
        )

        for idx in range(1, plan.count + 1):
            TASK_QUEUE.submit(
                Task(
                    name=f"exposure_{idx}",
                    func=lambda i=idx: self._run_exposure(plan, i),
                    retries=3,
                    backoff_seconds=2.0,
                )
            )

        if plan.park_after:
            total_duration = max(plan.exposure_seconds * plan.count + 30.0, 0.0)
            threading.Thread(target=self._park_after, args=(total_duration,), daemon=True).start()

        return {
            "target": plan.target,
            "started_at": started_at.isoformat(),
            "expected_paths": [],  # No longer predicting exact paths
            "park_after": plan.park_after,
        }

    def _ensure_devices_ready(self) -> None:
        """Validate that telescope/camera are already connected before issuing commands."""
        status = self.bridge.get_status()
        nina_status = status.get("nina_status") or {}
        telescope = nina_status.get("telescope") or {}
        camera = nina_status.get("camera") or {}
        missing: list[str] = []
        if not telescope.get("is_connected"):
            missing.append("telescope")
        if not camera.get("is_connected"):
            missing.append("camera")
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"reason": "device_disconnected", "devices": missing},
            )

    def _park_after(self, delay_seconds: float) -> None:
        logger.info("Automation: will park after %.1fs", delay_seconds)
        time.sleep(delay_seconds)
        try:
            SESSION_STATE.log_event("Automation: Parking telescope", "info")
            self.bridge.park_telescope(True)
            logger.info("Automation: telescope parked")
        except Exception as exc:  # pragma: no cover - keep background thread silent
            logger.warning("Automation: failed to park telescope: %s", exc)

    def _run_exposure(self, plan: AutomationPlan, index: int) -> None:
        """Fire a single exposure via the bridge."""
        SESSION_STATE.log_event(
            f"Automation: Starting exposure {index}/{plan.count} for {plan.target}", "info"
        )
        self.bridge.wait_for_mount_ready()
        self.bridge.wait_for_camera_idle()
        result = self.bridge.start_exposure(
            filter_name=plan.filter,
            binning=plan.binning,
            exposure_seconds=plan.exposure_seconds,
            target=plan.target,
        )
        if not isinstance(result, dict):
            raise RuntimeError("NINA capture returned unexpected payload")
        # Note: NINA API does not return file paths - will be None
        # File path will be filled by file system monitoring service
        file_path = result.get("file")
        platesolve = result.get("platesolve")
        started_at = datetime.utcnow().isoformat()
        SESSION_STATE.add_capture(
            {
                "kind": "exposure",
                "target": plan.target,
                "sequence": plan.target,
                "index": index,
                "started_at": started_at,
                "path": file_path or "",  # Empty initially, filled by file monitor
                "platesolve": platesolve,
            }
        )
        if platesolve:
            success = platesolve.get("Success")
            coords = (platesolve.get("Coordinates") or {})
            coord_str = ""
            ra = coords.get("RADegrees")
            dec = coords.get("DECDegrees")
            if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
                coord_str = f" RA {ra:.3f}° Dec {dec:.3f}°"
            if success:
                SESSION_STATE.log_event(
                    f"NINA solve succeeded for {plan.target}.{coord_str}",
                    "good",
                )
            else:
                SESSION_STATE.log_event(
                    f"NINA solve failed for {plan.target} – local solver will retry",
                    "warn",
                )
        else:
            SESSION_STATE.log_event(
                f"NINA solve result unavailable for {plan.target}",
                "warn",
            )

    def build_sequential_target_plan(
        self,
        targets: list[dict[str, Any]],
        name: str | None = None,
        park_after: bool = False,
    ) -> SequentialTargetPlan:
        """
        Build a sequential target plan from a list of target candidates.

        Each target dict should have: name, ra_deg, dec_deg, vmag (optional)
        Presets will be applied based on vmag for each target.
        """
        profile = None
        try:
            profile = get_active_equipment_profile()
        except Exception:
            pass

        sequence_name = name or f"NEOCP-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        planned_targets = []
        now = datetime.utcnow()
        with get_session() as session:
            predictor = EphemerisPredictionService(session)
            for target in targets:
                preset = select_preset(target.get("vmag"), profile=profile)
                predicted_coords = predictor.predict(target.get("candidate_id"), now)
                ra_deg = predicted_coords[0] if predicted_coords else target["ra_deg"]
                dec_deg = predicted_coords[1] if predicted_coords else target["dec_deg"]

                target_name = (
                    target.get("name")
                    or target.get("target")
                    or target.get("trksub")
                    or target.get("sequence_name")
                )
                planned_target = {
                    "name": target_name or "Unknown",
                    "candidate_id": target.get("candidate_id"),
                    "ra_deg": ra_deg,
                    "dec_deg": dec_deg,
                    "filter_name": preset.filter,
                    "binning": preset.binning,
                    "exposure_seconds": preset.exposure_seconds,
                    "count": preset.count,
                    "gain": preset.gain,
                    "offset": preset.offset,
                    "sequence_name": sequence_name,
                }
                planned_targets.append(planned_target)

        return SequentialTargetPlan(
            name=sequence_name,
            targets=planned_targets,
            park_after=park_after,
        )

    def run_sequential_target_sequence(self, plan: SequentialTargetPlan) -> dict[str, Any]:
        """
        Execute targets sequentially, one at a time.

        This method:
        1. Ensures weather is safe and devices are ready
        2. For each target:
           a. Sends single-target sequence to NINA
           b. Waits for all images from that target
           c. Plate-solves any images NINA didn't solve
           d. Moves to next target
        3. Optionally parks the telescope when all targets complete
        """
        self._ensure_weather_safe()
        self._ensure_devices_ready()

        started_at = datetime.utcnow()
        guiding_started = False
        target_names: list[str] = [t["name"] for t in plan.targets]
        total_exposures = sum(t["count"] for t in plan.targets)

        try:
            try:
                self.bridge.start_guiding()
                guiding_started = True
                SESSION_STATE.log_event("Guiding started for sequential sequence", "info")
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("Automation: failed to start guiding: %s", exc)
                SESSION_STATE.log_event("Guiding could not be enabled for sequence", "warn")

            SESSION_STATE.log_event(
                f"🎯 Starting sequential observation of {len(plan.targets)} targets, {total_exposures} total exposures",
                "info"
            )

            for idx, target in enumerate(plan.targets, 1):
                SESSION_STATE.log_event(
                    f"  {idx}. {target['name']}: {target['count']}×{target['exposure_seconds']:.0f}s @ {target['filter_name']}",
                    "info"
                )

            loop_results: list[CaptureLoopResult] = []

            for idx, target in enumerate(plan.targets, 1):
                SESSION_STATE.log_event(
                    f"📍 Target {idx}/{len(plan.targets)}: {target['name']}",
                    "info"
                )

                descriptor = CaptureTargetDescriptor(
                    name=target["name"],
                    candidate_id=target.get("candidate_id"),
                    ra_deg=target["ra_deg"],
                    dec_deg=target["dec_deg"],
                    filter_name=target["filter_name"],
                    binning=target["binning"],
                    exposure_seconds=target["exposure_seconds"],
                    count=target["count"],
                    sequence_name=plan.name,
                )

                try:
                    result = run_capture_loop(descriptor, self.bridge)
                    loop_results.append(result)
                    SESSION_STATE.log_event(
                        f"✓ Completed {descriptor.name} ({idx}/{len(plan.targets)})",
                        "good"
                    )
                except Exception as e:
                    logger.error(f"Error processing target {target['name']}: {e}", exc_info=True)
                    SESSION_STATE.log_event(
                        f"✗ Failed to process {target['name']}: {e}",
                        "error"
                    )
                    continue

            total_attempted = sum(result.exposures_attempted for result in loop_results)
            total_solved = sum(result.exposures_solved for result in loop_results)

            SESSION_STATE.log_event(
                f"🏁 Sequential observation complete: {len(loop_results)}/{len(plan.targets)} targets processed, {total_solved}/{total_attempted} exposures solved",
                "good" if len(loop_results) == len(plan.targets) else "warn"
            )

            if plan.park_after:
                SESSION_STATE.log_event("🅿 Parking telescope...", "info")
                try:
                    self.bridge.park_telescope(True)
                    SESSION_STATE.log_event("✓ Telescope parked", "good")
                except Exception as e:
                    logger.warning(f"Failed to park telescope: {e}")
                    SESSION_STATE.log_event(f"✗ Park failed: {e}", "warn")

            return {
                "sequence_name": plan.name,
                "targets": target_names,
                "started_at": started_at.isoformat(),
                "completed_at": datetime.utcnow().isoformat(),
                "targets_processed": len(loop_results),
                "total_exposures_attempted": total_attempted,
                "total_exposures_solved": total_solved,
                "park_after": plan.park_after,
            }
        finally:
            if guiding_started:
                try:
                    self.bridge.stop_guiding()
                    SESSION_STATE.log_event("Guiding stopped after sequential sequence", "info")
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("Automation: failed to stop guiding: %s", exc)
                    SESSION_STATE.log_event("Guiding could not be stopped cleanly", "warn")


__all__ = ["AutomationPlan", "SequentialTargetPlan", "AutomationService"]
