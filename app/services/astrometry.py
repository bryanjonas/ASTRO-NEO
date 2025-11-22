"""Astrometry orchestration: run solve-field and persist results."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from sqlmodel import Session

from app.db.session import get_session
from app.models import AstrometricSolution, CaptureLog
from app.services.solver import SolveError, solve_fits


class AstrometryService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def solve_capture(
        self,
        capture_id: int | None = None,
        path: str | Path | None = None,
        ra_hint: float | None = None,
        dec_hint: float | None = None,
        radius_deg: float | None = None,
        downsample: int | None = None,
    ) -> AstrometricSolution:
        def _run(db: Session) -> AstrometricSolution:
            capture = None
            solve_path: Path
            if capture_id is not None:
                capture = db.get(CaptureLog, capture_id)
                if not capture:
                    raise ValueError("capture_not_found")
                solve_path = Path(capture.path)
            elif path is not None:
                solve_path = Path(path)
            else:
                raise ValueError("path_required")

            started = time.perf_counter()
            try:
                result = solve_fits(
                    solve_path,
                    radius_deg=radius_deg,
                    ra_hint=ra_hint,
                    dec_hint=dec_hint,
                    downsample=downsample,
                )
                duration = time.perf_counter() - started
                fields = self._extract_fields(result)
                model = AstrometricSolution(
                    capture_id=capture.id if capture else None,
                    path=str(solve_path),
                    ra_deg=fields.get("ra_deg"),
                    dec_deg=fields.get("dec_deg"),
                    orientation_deg=fields.get("orientation_deg"),
                    pixel_scale_arcsec=fields.get("pixel_scale_arcsec"),
                    uncertainty_arcsec=fields.get("uncertainty_arcsec"),
                    duration_seconds=duration,
                    success=True,
                    solver_info=json.dumps(result),
                )
            except SolveError as exc:
                duration = time.perf_counter() - started
                model = AstrometricSolution(
                    capture_id=capture.id if capture else None,
                    path=str(solve_path),
                    success=False,
                    duration_seconds=duration,
                    solver_info=json.dumps({"error": str(exc)}),
                )

            db.add(model)
            db.commit()
            db.refresh(model)
            return model

        if self.session:
            return _run(self.session)
        with get_session() as db:
            return _run(db)

    def _extract_fields(self, payload: dict[str, Any]) -> dict[str, float | None]:
        solution = payload.get("solution") or {}
        ra = solution.get("ra") or solution.get("ra_hmsdeg")
        dec = solution.get("dec") or solution.get("dec_dmsdeg")
        return {
            "ra_deg": float(ra) if ra is not None else None,
            "dec_deg": float(dec) if dec is not None else None,
            "orientation_deg": _safe_float(solution.get("orientation")),
            "pixel_scale_arcsec": _safe_float(solution.get("pixscale")),
            "uncertainty_arcsec": _safe_float(solution.get("rms")),
        }


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["AstrometryService"]
