"""Helpers for fetching and caching MPC ephemerides."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Sequence

import httpx
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.core.site_config import SiteFileConfig
from app.models import NeoCandidate, NeoEphemeris

logger = logging.getLogger(__name__)


class MpcEphemerisClient:
    """Fetch and cache per-minute ephemerides for NEOCP candidates."""

    def __init__(self, session: Session, site_config: SiteFileConfig) -> None:
        self.session = session
        self.site_config = site_config

    def get_or_fetch(
        self,
        candidate: NeoCandidate,
        start_utc: datetime,
        end_utc: datetime,
        expected_count: int,
        sample_minutes: int,
    ) -> list[NeoEphemeris]:
        """Return cached rows, fetching from MPC when needed."""

        rows = self._load_rows(candidate, start_utc, end_utc)
        if len(rows) >= expected_count:
            return rows

        try:
            payload = self._fetch_remote(candidate.trksub, start_utc, end_utc, sample_minutes)
        except Exception as exc:  # pragma: no cover - network errors
            logger.warning("Ephemeris fetch failed for %s: %s", candidate.trksub, exc)
            return rows

        if payload:
            self._persist_rows(candidate, payload, start_utc, end_utc)
            rows = self._load_rows(candidate, start_utc, end_utc)
        return rows

    def _load_rows(
        self,
        candidate: NeoCandidate,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[NeoEphemeris]:
        if candidate.id is None:
            return []
        stmt = (
            select(NeoEphemeris)
            .where(
                NeoEphemeris.candidate_id == candidate.id,
                NeoEphemeris.epoch >= start_utc,
                NeoEphemeris.epoch <= end_utc,
            )
            .order_by(NeoEphemeris.epoch)
        )
        return list(self.session.exec(stmt).all())

    def _fetch_remote(
        self,
        trksub: str,
        start_utc: datetime,
        end_utc: datetime,
        sample_minutes: int,
    ) -> Sequence[dict]:
        payload = {
            "trksub": trksub,
            "start_time": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "step_minutes": sample_minutes,
            "latitude": self.site_config.latitude,
            "longitude": self.site_config.longitude,
            "elevation_m": self.site_config.altitude_m,
            "format": "json",
        }
        response = httpx.post(
            settings.mpc_ephemeris_url,
            json=payload,
            timeout=settings.mpc_ephemeris_timeout,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            entries = data.get("ephemeris") or data.get("results")
            if entries is None:
                raise RuntimeError("Unexpected MPC ephemeris response format")
            return entries
        if isinstance(data, list):
            return data
        raise RuntimeError("Unable to parse MPC ephemeris response")

    def _persist_rows(
        self,
        candidate: NeoCandidate,
        rows: Iterable[dict],
        start_utc: datetime,
        end_utc: datetime,
    ) -> None:
        if candidate.id is None:
            return
        self.session.exec(
            delete(NeoEphemeris).where(
                NeoEphemeris.candidate_id == candidate.id,
                NeoEphemeris.epoch >= start_utc,
                NeoEphemeris.epoch <= end_utc,
            )
        )
        for entry in rows:
            epoch = _parse_epoch(entry)
            if epoch is None:
                continue
            model = NeoEphemeris(
                candidate_id=candidate.id,
                trksub=candidate.trksub,
                epoch=epoch,
                ra_deg=_parse_float(entry.get("ra_deg") or entry.get("ra")),
                dec_deg=_parse_float(entry.get("dec_deg") or entry.get("dec")),
                delta_au=_parse_float(entry.get("delta_au") or entry.get("delta")),
                r_au=_parse_float(entry.get("r_au") or entry.get("r")),
                rate_arcsec_per_min=_parse_float(
                    entry.get("rate_arcsec_per_min") or entry.get("ang_rate")
                ),
                position_angle_deg=_parse_float(
                    entry.get("position_angle_deg") or entry.get("pa")
                ),
                magnitude=_parse_float(entry.get("magnitude") or entry.get("vmag")),
            )
            if model.ra_deg is None or model.dec_deg is None:
                continue
            self.session.add(model)


def _parse_epoch(entry: dict) -> datetime | None:
    value = entry.get("epoch_iso") or entry.get("time") or entry.get("epoch")
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value).replace(second=0, microsecond=0)
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.astimezone(tz=None).replace(tzinfo=None, second=0, microsecond=0)


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


__all__ = ["MpcEphemerisClient"]
