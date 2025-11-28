"""Shared helpers for kicking off nightly automation runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import select

from app.db.session import get_session
from app.models import NeoCandidate, NeoObservability
from app.services.automation import AutomationService
from app.services.session import SESSION_STATE


@dataclass
class NightSessionError(Exception):
    status_code: int
    message: str

    def __str__(self) -> str:  # pragma: no cover - human-friendly
        return self.message


def kickoff_imaging() -> dict[str, Any]:
    """Select the configured target (auto/manual) and start automation."""

    target = _choose_target()
    if not target:
        raise NightSessionError(status_code=404, message="No targets are currently observable (check time window, weather, or switch to Manual).")
    automation = AutomationService()
    urgency = None
    if target.get("score") is not None:
        urgency = max(0.0, min(1.0, target["score"] / 100.0))
    plan = automation.build_plan(
        target=target["trksub"],
        ra_deg=target["ra_deg"],
        dec_deg=target["dec_deg"],
        vmag=target.get("vmag"),
        urgency=urgency,
    )
    return automation.run_plan(plan)


def _choose_target() -> dict[str, Any] | None:
    """Return the manually selected trksub or the highest-ranked target."""

    mode = SESSION_STATE.target_mode
    if mode == "manual":
        trksub = SESSION_STATE.selected_target
        if not trksub:
            raise NightSessionError(status_code=400, message="Manual mode selected but no target chosen.")
        target = _fetch_target(trksub)
        if not target:
            raise NightSessionError(status_code=404, message="Selected target is no longer visible.")
        return target
    return _fetch_target()


def _fetch_target(trksub: str | None = None) -> dict[str, Any] | None:
    """Fetch a target entry with coordinates suitable for automation."""
    from app.services.observability import ObservabilityService

    with get_session() as session:
        stmt = (
            select(NeoObservability, NeoCandidate)
            .join(NeoCandidate, NeoCandidate.id == NeoObservability.candidate_id)
            .order_by(NeoObservability.score.desc())
        )
        if trksub:
            stmt = stmt.where(NeoObservability.trksub == trksub)
        else:
            stmt = stmt.where(NeoObservability.is_observable.is_(True))
            
            # Check if a custom window is active in the service (singleton-ish usage pattern here is tricky)
            # Actually, ObservabilityService is instantiated per request usually.
            # But the dashboard refresh endpoint updated the DB records based on a custom window.
            # So the 'is_observable' flag and 'window_start/end' in DB now reflect that custom window.
            
            # However, we are filtering by 'now' here to ensure we only pick targets currently visible.
            # If the user simulated a daytime window (e.g. 09:00-17:00), the DB records say "observable"
            # but window_start might be 09:00. If 'now' is 08:00, this check fails.
            
            # If the user wants to "simulate" the run, we should probably check if we are in a "simulation" mode
            # or if we should trust the DB's window_start/end implicitly if they were just updated?
            
            # The issue is: _fetch_target uses datetime.utcnow() (real time).
            # If the user set a custom window in the future/past, the DB records are updated,
            # but this check: `stmt.where(NeoObservability.window_start <= now)` prevents selection
            # if 'now' is not inside that window.
            
            # To fix the "Overview" indicator (which calls this), we need to know if we should ignore 'now'.
            # But 'kickoff_imaging' (Auto-Pilot) relies on this to not slew to something not visible *now*.
            
            # If the user's intent with "Update Window" is just to see what *would* be available, 
            # then the Targets tab shows that correctly (it lists everything with is_observable=True).
            # The Overview tab "Target: None" is technically correct because you can't image it *now*.
            
            # BUT, if the user wants to force a run during that window, they can't unless 'now' matches.
            
            # If the user request implies "I want to see if targets are available *in that window*",
            # then the Overview indicator is misleading if it only checks 'now'.
            
            # Let's relax the 'now' check if the window seems to be a custom one? 
            # Or better, let's just trust the DB 'is_observable' flag for the indicator?
            # No, because 'is_observable' just means "visible at some point".
            
            # If the user wants to know if they can *start* auto-pilot, the 'now' check is essential.
            # If they just want to see "Green" status because they found targets for tonight, 
            # we might need a different indicator or logic.
            
            # However, the user said: "I'm update my time window to daytime... it doesn't change the overview target indicator."
            # This implies they expect the indicator to reflect availability *within that window*, not necessarily *now*.
            # OR, they set the window to *now* (daytime) and expected to see targets?
            # If they set it to daytime, and it's daytime, then 'now' should be inside the window.
            
            # Wait, if they set window to 09:00-17:00 and it is 12:00.
            # ObservabilityService calculates windows.
            # If the target is visible 09:00-17:00, window_start=09:00, window_end=17:00.
            # 'now' (12:00) >= window_start AND 'now' < window_end.
            # So it SHOULD work.
            
            # Why did it not work?
            # Maybe because `_check_target_availability` in session.py calls `_choose_target` -> `_fetch_target`.
            # And `_fetch_target` uses `datetime.utcnow()`.
            
            # If the user updated the window via the dashboard, `ObservabilityService.refresh()` was called.
            # This updates the `NeoObservability` table.
            # So `window_start` and `window_end` in DB should be correct for the custom window.
            
            # If the user set the window to "daytime" (e.g. now), and targets were found (Targets tab shows them),
            # then `is_observable` is True.
            
            # Is it possible `window_start` is not what we think?
            # `ObservabilityService` calculates `window_start` from the `time_grid`.
            # If `set_window` was used, `time_grid` covers the custom window.
            # So `window_start` should be within that range.
            
            # Let's debug by logging or just removing the 'now' check for a moment to see?
            # No, removing 'now' check breaks the "can I image now?" contract.
            
            # Perhaps the issue is that `ObservabilityService` filters out "sun_above_limit" by default?
            # If they set the window to daytime, but the sun is up, `ObservabilityService` will block everything
            # unless we ALSO ignore sun constraints?
            # The user didn't say they changed the sun constraint.
            # If they just changed the time, but it's day, the sun block is still active.
            # So no targets would be `is_observable`.
            
            # Check `app/services/observability.py`:
            # `sun_ok = self.sun_altitudes <= self.max_sun_altitude`
            # If custom window is daytime, `sun_ok` will be False.
            # So `is_observable` will be False.
            # So `_fetch_target` returns nothing.
            # So indicator stays "None".
            
            # If the user *wants* to test daytime imaging, they need to ignore sun constraints.
            # Or maybe they just meant "a time when targets are visible" (e.g. tonight) but they are checking it during the day?
            # "I'm update my time window to daytime" -> ambiguous.
            # If they mean "I set the window to be *now* (which is day)", then Sun blocks it.
            # If they mean "I set the window to be *tonight* (when it's dark)", but I'm checking it *now* (day).
            # Then `window_start` (tonight) > `now` (day).
            # So `stmt.where(NeoObservability.window_start <= now)` fails.
            
            # The user likely wants the "Target Available" indicator to tell them "Do I have a target for the selected window?"
            # rather than "Can I start right this second?".
            # But the "Start Auto-Pilot" button implies starting *now*.
            
            # If we change the indicator to "Target Ready (for window)", it might be better.
            # But `_fetch_target` is used by `kickoff_imaging`.
            
            # Let's modify `_check_target_availability` in `app/api/session.py` to NOT check the time window
            # if we just want to know if a target exists for the *session*.
            # But `_check_target_availability` calls `_choose_target` which calls `_fetch_target`.
            
            # We should probably add a flag to `_fetch_target` to ignore the current time check.
            pass

    # We will modify the function signature to accept an `ignore_time` flag.
    # But we can't easily change the signature in `night_ops.py` without updating callers.
    # Let's check `_check_target_availability` in `session.py` again.
    
    return _fetch_target_internal(trksub)


def _fetch_target_internal(trksub: str | None = None, ignore_time: bool = False) -> dict[str, Any] | None:
    with get_session() as session:
        stmt = (
            select(NeoObservability, NeoCandidate)
            .join(NeoCandidate, NeoCandidate.id == NeoObservability.candidate_id)
            .order_by(NeoObservability.score.desc())
        )
        if trksub:
            stmt = stmt.where(NeoObservability.trksub == trksub)
        else:
            stmt = stmt.where(NeoObservability.is_observable.is_(True))
            if not ignore_time:
                now = datetime.utcnow()
                stmt = stmt.where(NeoObservability.window_start <= now)
                stmt = stmt.where(NeoObservability.window_end > now)
            stmt = stmt.limit(1)
        row = session.exec(stmt).first()
    if not row:
        return None
    obs, cand = row
    if cand.ra_deg is None or cand.dec_deg is None:
        raise NightSessionError(status_code=400, message="Target is missing coordinates (ra/dec).")
    return {
        "trksub": obs.trksub,
        "ra_deg": cand.ra_deg,
        "dec_deg": cand.dec_deg,
        "vmag": cand.vmag,
        "score": obs.score,
        "window_start": obs.window_start,
    }


__all__ = ["kickoff_imaging", "NightSessionError", "_fetch_target_internal"]
