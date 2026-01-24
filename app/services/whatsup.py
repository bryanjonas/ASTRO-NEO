"""MPC WhatsUp target ingestion and Horizons caching."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from lxml import html
from sqlmodel import Session, select

from app.core.config import settings
from app.core.site_config import SiteFileConfig, load_site_config
from app.models import NeoCandidate, NeoEphemeris
from app.services.horizons_client import HorizonsClient
from app.services.scout_client import ScoutClient

logger = logging.getLogger(__name__)

WHATSUP_URL = "https://minorplanetcenter.net/whatsup/index"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.6 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://minorplanetcenter.net/",
    "Connection": "keep-alive",
}

COLUMN_SCHEMA = [
    "designation",
    "mag",
    "solar_elong",
    "lunar_elong",
    "begin_time",
    "begin_ra",
    "begin_dec",
    "begin_alt",
    "max_time",
    "max_ra",
    "max_dec",
    "max_alt",
    "end_time",
    "end_ra",
    "end_dec",
    "end_alt",
]


@dataclass
class WhatsUpTargetPayload:
    candidate_id: str
    designation: str
    vmag: float | None
    solar_elongation: float | None
    lunar_elongation: float | None
    raw_entry: str | None


def _normalize_designation(designation: str) -> str:
    trimmed = designation.strip()
    if trimmed.startswith("(") and trimmed.endswith(")"):
        return trimmed[1:-1].strip()
    return trimmed


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class WhatsUpService:
    """Fetch WhatsUp targets and cache Horizons ephemerides."""

    def __init__(self, session: Session, site_config: SiteFileConfig | None = None) -> None:
        self.session = session
        self.site_config = site_config or load_site_config()
        self.horizons = HorizonsClient(
            site_lat=self.site_config.latitude,
            site_lon=self.site_config.longitude,
            site_alt_m=self.site_config.altitude_m,
            timeout=settings.horizons_timeout,
        )
        self.scout = ScoutClient(
            obs_code=self.site_config.station_code,
            timeout=settings.scout_timeout,
            base_url=settings.scout_api_url,
        )

    def fetch_targets(self, when: datetime | None = None) -> list[WhatsUpTargetPayload]:
        if when is None:
            observed_at = datetime.now(timezone.utc)
        else:
            if when.tzinfo is None:
                observed_at = when.replace(tzinfo=timezone.utc)
            else:
                observed_at = when.astimezone(timezone.utc)
        payload = {
            "utf8": "✓",
            "latitude": f"{self.site_config.latitude:.6f}",
            "longitude": f"{self.site_config.longitude:.6f}",
            "year": str(observed_at.year),
            "month": str(observed_at.month),
            "day": str(observed_at.day),
            "hour": str(observed_at.hour),
            "minute": str(observed_at.minute),
            "duration": str(int(settings.whatsup_duration_hours)),
            "max_objects": str(settings.whatsup_max_objects),
            "min_alt": str(settings.whatsup_min_altitude_deg),
            "solar_elong": str(settings.whatsup_solar_elongation_deg),
            "lunar_elong": str(settings.whatsup_lunar_elongation_deg),
            "object_type": settings.whatsup_object_type,
            "submit": "Submit",
        }

        with httpx.Client(timeout=settings.whatsup_timeout) as client:
            response = client.get(WHATSUP_URL, headers=HEADERS)
            response.raise_for_status()
            doc = html.fromstring(response.text)
            token_nodes = doc.xpath("//input[@name='authenticity_token']/@value")
            if not token_nodes:
                raise RuntimeError("WhatsUp CSRF token not found")
            payload["authenticity_token"] = token_nodes[0]

            response = client.post(WHATSUP_URL, headers=HEADERS, data=payload)
            response.raise_for_status()

        doc = html.fromstring(response.text)
        tables = doc.xpath("//table")
        if not tables:
            raise RuntimeError("WhatsUp response missing results table")
        rows = tables[-1].xpath(".//tr")

        targets: list[WhatsUpTargetPayload] = []
        for row in rows:
            cells = [cell.text_content().strip() for cell in row.xpath(".//td")]
            if len(cells) != len(COLUMN_SCHEMA):
                continue
            record = dict(zip(COLUMN_SCHEMA, cells))
            designation = record["designation"]
            candidate_id = _normalize_designation(designation)
            raw_entry = json.dumps(record, ensure_ascii=True)
            targets.append(
                WhatsUpTargetPayload(
                    candidate_id=candidate_id,
                    designation=designation,
                    vmag=_parse_float(record.get("mag")),
                    solar_elongation=_parse_float(record.get("solar_elong")),
                    lunar_elongation=_parse_float(record.get("lunar_elong")),
                    raw_entry=raw_entry,
                )
            )
        return targets

    def refresh_targets(self, when: datetime | None = None) -> list[NeoCandidate]:
        payloads = self.fetch_targets(when=when)
        return self._sync_targets(payloads)

    def ensure_targets(self, force: bool = False) -> list[NeoCandidate]:
        if force:
            return self.refresh_targets()
        cutoff = datetime.utcnow() - timedelta(minutes=settings.whatsup_refresh_minutes)
        existing = self.session.exec(
            select(NeoCandidate)
            .where(NeoCandidate.status == "WHATSUP")
            .where(NeoCandidate.updated_at >= cutoff)
        ).all()
        if existing:
            return list(existing)
        return self.refresh_targets()

    def get_ranked_targets(self, limit: int) -> list[NeoCandidate]:
        candidates = list(
            self.session.exec(
                select(NeoCandidate).where(NeoCandidate.status == "WHATSUP")
            ).all()
        )
        candidates.sort(
            key=lambda item: (item.vmag is None, -(item.vmag or 0.0)),
        )
        return candidates[:limit]

    def ensure_horizons_cache(
        self,
        targets: Iterable[NeoCandidate],
    ) -> None:
        cutoff = datetime.utcnow() - timedelta(
            minutes=settings.whatsup_horizons_ttl_minutes
        )
        for candidate in targets:
            existing = self.session.exec(
                select(NeoEphemeris)
                .where(NeoEphemeris.candidate_id == candidate.id)
                .where(NeoEphemeris.source == "HORIZONS")
                .where(NeoEphemeris.epoch >= cutoff)
                .order_by(NeoEphemeris.epoch.desc())
            ).first()
            if existing:
                continue
            try:
                row = self.horizons.get_current_position(candidate.id)
                self._upsert_ephemeris(candidate, row, source="HORIZONS")
            except Exception as exc:
                logger.warning("Horizons fetch failed for %s: %s", candidate.id, exc)

    def refresh_targets_with_horizons(self) -> list[NeoCandidate]:
        targets = self.refresh_targets()
        successful: list[NeoCandidate] = []
        for candidate in targets:
            try:
                row = self.horizons.get_current_position(candidate.id)
                self._upsert_ephemeris(candidate, row, source="HORIZONS")
                max_altitude = settings.max_target_altitude_deg
                elevation = row.get("elevation_deg")
                if (
                    max_altitude is not None
                    and elevation is not None
                    and float(elevation) >= float(max_altitude)
                ):
                    candidate.status = "WHATSUP_TOO_HIGH"
                    logger.warning(
                        "Skipping %s: elevation %.1f° exceeds max %.1f°",
                        candidate.id,
                        float(elevation),
                        float(max_altitude),
                    )
                    continue
                candidate.status = "WHATSUP"
                successful.append(candidate)
            except Exception as exc:
                logger.warning("Horizons fetch failed for %s: %s", candidate.id, exc)
                candidate.status = "WHATSUP_NO_HORIZONS"
        self.session.commit()
        return successful

    def missing_horizons(self, targets: Iterable[NeoCandidate]) -> list[str]:
        cutoff = datetime.utcnow() - timedelta(
            minutes=settings.whatsup_horizons_ttl_minutes
        )
        missing: list[str] = []
        for candidate in targets:
            existing = self.session.exec(
                select(NeoEphemeris)
                .where(NeoEphemeris.candidate_id == candidate.id)
                .where(NeoEphemeris.source == "HORIZONS")
                .where(NeoEphemeris.epoch >= cutoff)
                .order_by(NeoEphemeris.epoch.desc())
            ).first()
            if not existing:
                missing.append(candidate.trksub or candidate.id)
        return missing

    def _sync_targets(self, payloads: Iterable[WhatsUpTargetPayload]) -> list[NeoCandidate]:
        results: list[NeoCandidate] = []
        now = datetime.utcnow()
        for payload in payloads:
            existing = self.session.exec(
                select(NeoCandidate).where(NeoCandidate.id == payload.candidate_id)
            ).first()
            if existing:
                existing.trksub = payload.designation
                existing.vmag = payload.vmag
                existing.status = "WHATSUP"
                existing.raw_entry = payload.raw_entry
                existing.updated_at = now
                results.append(existing)
            else:
                model = NeoCandidate(
                    id=payload.candidate_id,
                    trksub=payload.designation,
                    vmag=payload.vmag,
                    status="WHATSUP",
                    raw_entry=payload.raw_entry,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(model)
                results.append(model)
        self.session.commit()
        for record in results:
            self.session.refresh(record)
        return results

    def _upsert_ephemeris(
        self,
        candidate: NeoCandidate,
        row: dict[str, Any],
        source: str,
    ) -> None:
        epoch = row["epoch"]
        existing = self.session.exec(
            select(NeoEphemeris)
            .where(NeoEphemeris.candidate_id == candidate.id)
            .where(NeoEphemeris.epoch == epoch)
            .where(NeoEphemeris.source == source)
        ).first()
        ra_rate = row.get("ra_rate_arcsec_min")
        dec_rate = row.get("dec_rate_arcsec_min")
        rate = None
        if ra_rate is not None and dec_rate is not None:
            rate = (float(ra_rate) ** 2 + float(dec_rate) ** 2) ** 0.5

        payload = {
            "ra_deg": row["ra_deg"],
            "dec_deg": row["dec_deg"],
            "ra_rate_arcsec_min": ra_rate,
            "dec_rate_arcsec_min": dec_rate,
            "rate_arcsec_per_min": rate,
            "azimuth_deg": row.get("azimuth_deg"),
            "elevation_deg": row.get("elevation_deg"),
            "airmass": row.get("airmass"),
            "v_mag_predicted": row.get("v_mag"),
            "uncertainty_3sigma_arcsec": row.get("uncertainty_3sigma_arcsec"),
            "source": source,
        }

        if existing:
            for key, value in payload.items():
                setattr(existing, key, value)
            self.session.add(existing)
        else:
            eph = NeoEphemeris(
                candidate_id=candidate.id,
                trksub=candidate.trksub,
                epoch=epoch,
                **payload,
            )
            self.session.add(eph)
        self.session.commit()
