"""Astrometry orchestration: run solve-field and persist results."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from photutils.detection import DAOStarFinder
from sqlmodel import Session

from app.core.config import settings
from app.db.session import get_session
from app.models import AstrometricSolution, CaptureLog, Measurement
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
                quality = self._run_photometry(solve_path)
                flags = self._collect_flags(fields, quality)
                model = AstrometricSolution(
                    capture_id=capture.id if capture else None,
                    target=capture.target if capture else None,
                    path=str(solve_path),
                    ra_deg=fields.get("ra_deg"),
                    dec_deg=fields.get("dec_deg"),
                    orientation_deg=fields.get("orientation_deg"),
                    pixel_scale_arcsec=fields.get("pixel_scale_arcsec"),
                    uncertainty_arcsec=fields.get("uncertainty_arcsec"),
                    snr=quality.get("snr") if quality else None,
                    mag_inst=quality.get("mag_inst") if quality else None,
                    flags=json.dumps(flags) if flags else None,
                    duration_seconds=duration,
                    success=not flags,
                    solver_info=json.dumps(result),
                )
                if capture:
                    self._persist_measurement(db, capture, fields, quality, flags)
            except SolveError as exc:
                duration = time.perf_counter() - started
                model = AstrometricSolution(
                    capture_id=capture.id if capture else None,
                    target=capture.target if capture else None,
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

    def _run_photometry(self, path: Path) -> dict[str, float] | None:
        try:
            data = fits.getdata(path)
        except Exception:
            return None
        if data is None:
            return None
        data = np.asarray(data, dtype=float)
        mean, median, std = sigma_clipped_stats(data, sigma=3.0)
        threshold = median + (5.0 * std)
        try:
            finder = DAOStarFinder(fwhm=4.0, threshold=threshold - median)
            sources = finder(data - median)
        except Exception:
            return None
        if sources is None or len(sources) == 0:
            return None
        # Take the brightest source as proxy
        brightest = sources[np.argmax(sources["flux"])]
        snr = float(brightest["peak"] / std) if std else None
        flux = float(brightest["flux"])
        mag_inst = -2.5 * np.log10(flux) if flux > 0 else None
        return {"snr": snr, "mag_inst": mag_inst}

    def _collect_flags(self, fields: dict[str, Any], quality: dict[str, Any] | None) -> list[str]:
        flags: list[str] = []
        rms = fields.get("uncertainty_arcsec")
        if rms is None:
            flags.append("missing_rms")
        elif rms > 5.0:
            flags.append("high_residual")
        if quality:
            snr = quality.get("snr")
            if snr is None:
                flags.append("missing_snr")
            elif snr < 5.0:
                flags.append("low_snr")
        return flags

    def _persist_measurement(
        self,
        db: Session,
        capture: CaptureLog,
        fields: dict[str, Any],
        quality: dict[str, Any] | None,
        flags: list[str],
    ) -> Measurement:
        station_code = settings.station_code
        observer = settings.observer_initials
        software = settings.software_id
        band = settings.default_band or "R"
        mag = quality.get("mag_inst") if quality else None
        snr = quality.get("snr") if quality else None
        mag_sigma = None
        if snr:
            mag_sigma = (1.0857 / snr) if snr > 0 else None
            if settings.mag_uncert_floor:
                mag_sigma = (mag_sigma**2 + settings.mag_uncert_floor**2) ** 0.5 if mag_sigma else settings.mag_uncert_floor
        meas = Measurement(
            capture_id=capture.id,
            target=capture.target or "unknown",
            obs_time=capture.started_at,
            ra_deg=fields.get("ra_deg") or 0.0,
            dec_deg=fields.get("dec_deg") or 0.0,
            ra_uncert_arcsec=fields.get("uncertainty_arcsec"),
            dec_uncert_arcsec=fields.get("uncertainty_arcsec"),
            magnitude=mag,
            mag_sigma=mag_sigma,
            band=band,
            exposure_seconds=None,
            tracking_mode=None,
            station_code=station_code,
            observer=observer,
            software=software,
            flags=json.dumps(flags) if flags else None,
            reviewed=False,
        )
        db.add(meas)
        db.commit()
        db.refresh(meas)
        return meas


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = ["AstrometryService"]
