"""Shared helpers for kicking off nightly automation runs."""

from __future__ import annotations

from dataclasses import dataclass
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
        raise NightSessionError(status_code=404, message="No visible targets available.")
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

    with get_session() as session:
        stmt = (
            select(NeoObservability, NeoCandidate)
            .join(NeoCandidate, NeoCandidate.id == NeoObservability.candidate_id)
            .order_by(NeoObservability.score.desc())
        )
        if trksub:
            stmt = stmt.where(NeoObservability.trksub == trksub)
        else:
            stmt = stmt.where(NeoObservability.is_observable.is_(True)).limit(1)
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
    }


__all__ = ["kickoff_imaging", "NightSessionError"]
