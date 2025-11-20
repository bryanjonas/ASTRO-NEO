"""Observability filtering and scoring utilities."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from astropy.coordinates import AltAz, SkyCoord, get_moon
from astropy.time import Time
import astropy.units as u
from astroplan import FixedTarget, Observer
from sqlmodel import Session, select

from app.core.config import settings
from app.core.site_config import SiteFileConfig, load_site_config
from app.models import NeoCandidate, NeoObservability, NeoEphemeris
from app.services.ephemeris import MpcEphemerisClient
from app.services.weather import WeatherService, WeatherSummary

logger = logging.getLogger(__name__)


@dataclass
class HorizonMask:
    """Simple interpolated horizon mask based on azimuth/altitude samples."""

    azimuths: np.ndarray
    altitudes: np.ndarray

    @classmethod
    def from_path(cls, path: str | None) -> "HorizonMask | None":
        if not path:
            return None
        mask_path = Path(path)
        if not mask_path.exists():
            logger.warning("Configured horizon mask %s not found", mask_path)
            return None
        try:
            data = json.loads(mask_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to parse horizon mask %s: %s", mask_path, exc)
            return None

        profile = data.get("outputs", {}).get("horizon_profile") or []
        if not profile:
            return None

        azimuths = []
        altitudes = []
        for entry in profile:
            azimuths.append(float(entry.get("A") or entry.get("azimuth") or 0.0))
            altitudes.append(float(entry.get("H_hor") or entry.get("altitude") or 0.0))

        if not azimuths:
            return None

        az = np.array([(az % 360.0) for az in azimuths], dtype=float)
        alts = np.array(altitudes, dtype=float)
        sort_idx = np.argsort(az)
        az = az[sort_idx]
        alts = alts[sort_idx]
        if az[0] > 0.0:
            az = np.insert(az, 0, 0.0)
            alts = np.insert(alts, 0, alts[0])
        if az[-1] < 360.0:
            az = np.append(az, 360.0)
            alts = np.append(alts, alts[0])
        return cls(azimuths=az, altitudes=alts)

    def limit_for(self, azimuths_deg: np.ndarray) -> np.ndarray:
        if not len(self.azimuths):
            return np.zeros_like(azimuths_deg)
        az_wrapped = np.mod(azimuths_deg, 360.0)
        return np.interp(az_wrapped, self.azimuths, self.altitudes)


class ObservabilityService:
    """Compute observability windows for MPC candidates."""

    def __init__(self, session: Session, site_config: SiteFileConfig | None = None) -> None:
        self.session = session
        self.site_config = site_config or load_site_config()
        self.observer = Observer(
            latitude=self.site_config.latitude * u.deg,
            longitude=self.site_config.longitude * u.deg,
            elevation=self.site_config.altitude_m * u.m,
            timezone="UTC",
        )
        self.sample_minutes = max(1, settings.observability_sample_minutes)
        self.horizon_hours = max(1, settings.observability_horizon_hours)
        self.min_altitude = settings.observability_min_altitude_deg
        self.max_sun_altitude = settings.observability_max_sun_altitude_deg
        self.min_moon_separation = settings.observability_min_moon_separation_deg
        self.max_vmag = settings.observability_max_vmag
        self.min_window_minutes = settings.observability_min_window_minutes
        self.target_window_minutes = settings.observability_target_window_minutes
        self.horizon_mask = HorizonMask.from_path(
            self.site_config.horizon_mask.source if self.site_config.horizon_mask else None
        )
        self.ephemeris_client = MpcEphemerisClient(session, self.site_config)
        self.weather_service = WeatherService(session, self.site_config)
        self.weather_summary: WeatherSummary | None = None

        self.time_grid = self._build_time_grid()
        datetime_grid = self.time_grid.to_datetime(timezone=timezone.utc)
        self.datetime_grid = [dt.replace(tzinfo=None, second=0, microsecond=0) for dt in datetime_grid]
        self.night_start = self.datetime_grid[0]
        self.night_end = self.datetime_grid[-1]
        self.night_key = self.night_start.date()
        self.expected_samples = len(self.datetime_grid)
        self.sun_altitudes = self.observer.sun_altaz(self.time_grid).alt.deg
        self.recent_hours = settings.observability_recent_hours

    def refresh(self, trksubs: Sequence[str] | None = None) -> list[NeoObservability]:
        """Recompute observability for the requested (or all) MPC candidates."""

        self.weather_summary = self.weather_service.get_status()
        stmt = select(NeoCandidate)
        if trksubs:
            stmt = stmt.where(NeoCandidate.trksub.in_(list(trksubs)))
        candidates = self.session.exec(stmt).all()
        results: list[NeoObservability] = []
        for candidate in candidates:
            result = self._evaluate_candidate(candidate)
            if result:
                results.append(result)
        self.session.commit()
        return results

    def _build_time_grid(self) -> Time:
        start_dt = datetime.utcnow().replace(second=0, microsecond=0)
        start = Time(start_dt, scale="utc")
        total_minutes = self.horizon_hours * 60
        offsets = np.arange(0, total_minutes + self.sample_minutes, self.sample_minutes)
        return start + offsets * u.minute

    def _get_ephemeris_coordinates(
        self, candidate: NeoCandidate
    ) -> tuple[SkyCoord | None, list[NeoEphemeris]]:
        if candidate.id is None:
            return None, []
        rows = self.ephemeris_client.get_or_fetch(
            candidate=candidate,
            start_utc=self.night_start,
            end_utc=self.night_end,
            expected_count=self.expected_samples,
            sample_minutes=self.sample_minutes,
        )
        if len(rows) < self.expected_samples:
            return None, rows

        lookup = {row.epoch.replace(second=0, microsecond=0): row for row in rows}
        ra_series: list[float] = []
        dec_series: list[float] = []
        for timestamp in self.datetime_grid:
            row = lookup.get(timestamp)
            if not row:
                return None, rows
            ra_series.append(row.ra_deg)
            dec_series.append(row.dec_deg)

        coords = SkyCoord(ra=np.array(ra_series) * u.deg, dec=np.array(dec_series) * u.deg)
        return coords, rows

    def _evaluate_candidate(self, candidate: NeoCandidate) -> NeoObservability | None:
        reasons: list[str] = []

        weather_summary = getattr(self, "weather_summary", None)
        if weather_summary and not weather_summary.is_safe:
            reasons.extend(weather_summary.reasons or ["weather_blocked"])
            return self._persist_result(candidate, None, reasons)

        if candidate.updated_at:
            age = datetime.utcnow() - candidate.updated_at
            if age > timedelta(hours=self.recent_hours):
                reasons.append("stale_candidate")
                return self._persist_result(candidate, None, reasons)

        if candidate.ra_deg is None or candidate.dec_deg is None:
            reasons.append("missing_coords")
            return self._persist_result(candidate, None, reasons)
        if candidate.vmag is not None and candidate.vmag > self.max_vmag:
            reasons.append("too_faint")
            return self._persist_result(candidate, None, reasons)

        ephem_coords, _ = self._get_ephemeris_coordinates(candidate)
        if ephem_coords is not None:
            altaz_frame = AltAz(obstime=self.time_grid, location=self.observer.location)
            altaz = ephem_coords.transform_to(altaz_frame)
            coords_for_moon: SkyCoord = ephem_coords
        else:
            target = FixedTarget(
                name=candidate.trksub,
                coord=SkyCoord(ra=candidate.ra_deg * u.deg, dec=candidate.dec_deg * u.deg),
            )
            altaz = self.observer.altaz(self.time_grid, target=target)
            coords_for_moon = target.coord

        altitudes = altaz.alt.deg
        azimuths = altaz.az.deg
        horizon_limits = (
            self.horizon_mask.limit_for(azimuths) if self.horizon_mask else np.zeros_like(altitudes)
        )
        altitude_ok = altitudes >= np.maximum(horizon_limits, self.min_altitude)
        sun_ok = self.sun_altitudes <= self.max_sun_altitude

        moon_coords = get_moon(self.time_grid, location=self.observer.location)
        moon_separation = coords_for_moon.separation(moon_coords).deg
        moon_ok = moon_separation >= self.min_moon_separation

        if not altitude_ok.any():
            reasons.append("below_horizon")
        if not sun_ok.any():
            reasons.append("sun_above_limit")
        if not moon_ok.any():
            reasons.append("moon_too_close")

        visibility_mask = altitude_ok & sun_ok & moon_ok
        windows = self._find_visibility_windows(visibility_mask)
        if not windows:
            return self._persist_result(candidate, None, reasons)

        best_start, best_end = max(windows, key=lambda pair: pair[1] - pair[0])
        best_mask = np.zeros_like(visibility_mask, dtype=bool)
        best_mask[best_start : best_end + 1] = True
        duration_minutes = (best_end - best_start + 1) * self.sample_minutes
        window_start = self.datetime_grid[best_start]
        window_end = self.datetime_grid[best_end] + timedelta(minutes=self.sample_minutes)

        if duration_minutes < self.min_window_minutes:
            reasons.append("window_too_short")

        max_altitude = float(np.max(altitudes[best_mask]))
        min_moon_sep = float(np.min(moon_separation[best_mask]))
        max_sun_alt = float(np.max(self.sun_altitudes[best_mask]))

        urgency_score = (candidate.score or 0) / 100.0
        duration_score = min(1.0, duration_minutes / self.target_window_minutes)
        altitude_score = min(1.0, max_altitude / 90.0)
        final_score = 0.0
        if duration_minutes >= self.min_window_minutes and not reasons:
            final_score = round(
                100.0 * (0.5 * duration_score + 0.3 * altitude_score + 0.2 * urgency_score),
                2,
            )

        breakdown = {
            "duration_minutes": duration_minutes,
            "duration_score": duration_score,
            "altitude_score": altitude_score,
            "urgency_score": urgency_score,
        }

        return self._persist_result(
            candidate,
            {
                "window_start": window_start,
                "window_end": window_end,
                "duration_minutes": duration_minutes,
                "max_altitude_deg": max_altitude,
                "min_moon_separation_deg": min_moon_sep,
                "max_sun_altitude_deg": max_sun_alt,
                "score": final_score,
                "score_breakdown": json.dumps(breakdown),
                "is_observable": duration_minutes >= self.min_window_minutes and not reasons,
                "limiting_factors": json.dumps(reasons) if reasons else None,
            },
            reasons,
        )

    def _find_visibility_windows(self, mask: np.ndarray) -> list[tuple[int, int]]:
        windows: list[tuple[int, int]] = []
        start_idx: int | None = None
        for idx, value in enumerate(mask):
            if value and start_idx is None:
                start_idx = idx
            elif not value and start_idx is not None:
                windows.append((start_idx, idx - 1))
                start_idx = None
        if start_idx is not None:
            windows.append((start_idx, len(mask) - 1))
        return windows

    def _persist_result(
        self,
        candidate: NeoCandidate,
        payload: dict | None,
        reasons: Iterable[str],
    ) -> NeoObservability | None:
        if candidate.id is None:
            return None

        stmt = select(NeoObservability).where(
            NeoObservability.candidate_id == candidate.id,
            NeoObservability.night_key == self.night_key,
        )
        existing = self.session.exec(stmt).first()
        base_fields = {
            "candidate_id": candidate.id,
            "trksub": candidate.trksub,
            "night_key": self.night_key,
            "night_start": self.night_start,
            "night_end": self.night_end,
            "computed_at": datetime.utcnow(),
            "is_observable": False,
            "limiting_factors": json.dumps(list(reasons)) if reasons else None,
        }
        if payload:
            base_fields.update(payload)

        if existing:
            for field, value in base_fields.items():
                setattr(existing, field, value)
            self.session.add(existing)
            return existing

        model = NeoObservability(**base_fields)
        self.session.add(model)
        return model


__all__ = ["ObservabilityService"]
