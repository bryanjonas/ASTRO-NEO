"""JPL Scout API client for NEOCP ephemerides."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
from astropy.coordinates import SkyCoord
import astropy.units as u

from app.core.config import settings

logger = logging.getLogger(__name__)


class ScoutClient:
    """Client for JPL Scout ephemerides (NEOCP tracklet IDs)."""

    def __init__(
        self,
        obs_code: str | None,
        timeout: float | None = None,
        base_url: str | None = None,
    ):
        self.obs_code = obs_code or "500"
        self.timeout = timeout or settings.scout_timeout
        self.base_url = base_url or settings.scout_api_url

    def fetch_ephemeris(
        self,
        tdes: str,
        start_time: datetime | str = "now",
        stop_time: datetime | str | None = None,
        step_minutes: int | None = None,
    ) -> list[dict[str, Any]]:
        logger.info("Fetching Scout ephemeris for %s (obs-code=%s)", tdes, self.obs_code)
        data = self._fetch_with_fallbacks(
            tdes=tdes,
            start_time=start_time,
            stop_time=stop_time,
            step_minutes=step_minutes,
        )

        rows = data.get("ephemeris") or data.get("eph") or data.get("data") or data.get("results") or []
        if isinstance(rows, dict):
            fields = rows.get("fields") or data.get("fields")
            data_rows = rows.get("data")
            if fields and isinstance(data_rows, list):
                return _rows_from_fields(fields, data_rows, tdes)
            logger.warning("Unexpected Scout ephemeris response shape for %s", tdes)
            return []
        if isinstance(rows, list):
            if rows and isinstance(rows[0], list):
                fields = data.get("fields")
                if fields:
                    return _rows_from_fields(fields, rows, tdes)
                logger.warning("Scout ephemeris list missing fields for %s", tdes)
                return []
            if rows and isinstance(rows[0], dict):
                converted = _rows_from_eph(rows, data, tdes)
                if converted:
                    return converted
                return rows
            if not rows:
                return rows
        logger.warning("Unexpected Scout ephemeris response shape for %s", tdes)
        return []

    def get_current_position(self, tdes: str) -> dict[str, Any]:
        rows = self.fetch_ephemeris(tdes=tdes, start_time="now")
        if not rows:
            raise Exception(f"Scout returned no ephemeris data for {tdes}")
        row = rows[0]
        try:
            ra_deg, dec_deg = self._parse_ra_dec(row)
        except Exception:
            logger.warning(
                "Scout ephemeris parse failed for %s; raw row=%s",
                tdes,
                row,
            )
            raise
        return {
            "epoch": self._parse_time(row) or datetime.utcnow(),
            "ra_deg": ra_deg,
            "dec_deg": dec_deg,
            "ra_rate_arcsec_min": self._parse_float(row, ["dra", "ra_rate", "dra_arcsec_min"]),
            "dec_rate_arcsec_min": self._parse_float(row, ["ddec", "dec_rate", "ddec_arcsec_min"]),
            "v_mag": self._parse_float(row, ["v", "vmag", "mag"]),
            "uncertainty_3sigma_arcsec": self._parse_float(row, ["sigma_pos", "sigma", "sig_pos"]),
        }

    def _format_time(self, value: datetime | str, use_space: bool = True) -> str:
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%d %H:%M" if use_space else "%Y-%m-%dT%H:%M")
        return value

    def _fetch_with_fallbacks(
        self,
        tdes: str,
        start_time: datetime | str,
        stop_time: datetime | str | None,
        step_minutes: int | None,
    ) -> dict[str, Any]:
        variants: list[dict[str, Any]] = []
        step_values: list[str | None] = []
        if step_minutes is None:
            step_values = [None]
        else:
            step_values = [f"{step_minutes}m", str(step_minutes)]

        for use_space in (True, False):
            for step_value in step_values:
                params: dict[str, Any] = {
                    "tdes": tdes,
                    "eph-start": self._format_time(start_time, use_space=use_space),
                    "obs-code": self.obs_code,
                }
                if stop_time is not None:
                    params["eph-stop"] = self._format_time(stop_time, use_space=use_space)
                if step_value is not None:
                    params["eph-step"] = step_value
                variants.append(params)

        if stop_time is not None or step_minutes is not None:
            variants.append(
                {
                    "tdes": tdes,
                    "eph-start": self._format_time("now", use_space=True),
                    "obs-code": self.obs_code,
                }
            )

        last_error: httpx.HTTPError | None = None
        for params in variants:
            try:
                response = httpx.get(self.base_url, params=params, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 400:
                    continue
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                raise
        if last_error is not None:
            raise last_error
        raise RuntimeError("Scout ephemeris request failed without a response")

    def _parse_time(self, row: dict[str, Any]) -> datetime | None:
        for key in ("time", "utc", "epoch", "datetime"):
            value = row.get(key)
            if not value:
                continue
            if isinstance(value, (int, float)):
                return datetime.utcfromtimestamp(value)
            text = str(value).replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(text).replace(tzinfo=None)
            except ValueError:
                continue
        return None

    def _parse_ra_dec(self, row: dict[str, Any]) -> tuple[float, float]:
        ra_val = (
            row.get("ra")
            or row.get("ra_deg")
            or row.get("ra_deg_apo")
            or row.get("ra_hours")
            or row.get("ra_hrs")
            or row.get("alpha")
            or row.get("ra_app")
        )
        dec_val = (
            row.get("dec")
            or row.get("dec_deg")
            or row.get("dec_deg_apo")
            or row.get("dec_deg_apparent")
            or row.get("delta")
            or row.get("dec_app")
        )
        if ra_val is None or dec_val is None:
            raise Exception("Scout ephemeris missing RA/Dec")
        try:
            ra = float(ra_val)
            dec = float(dec_val)
            if "ra_hours" in row or "ra_hrs" in row:
                ra *= 15.0
            return ra, dec
        except (TypeError, ValueError):
            coord = SkyCoord(str(ra_val), str(dec_val), unit=(u.hourangle, u.deg))
            return float(coord.ra.deg), float(coord.dec.deg)

    def _parse_float(self, row: dict[str, Any], keys: list[str]) -> float | None:
        for key in keys:
            value = row.get(key)
            if value is None:
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return None


def _rows_from_fields(fields: list[Any], rows: list[Any], tdes: str) -> list[dict[str, Any]]:
    if not isinstance(fields, list):
        logger.warning("Scout ephemeris fields missing/invalid for %s", tdes)
        return []
    cleaned_fields = [str(field).strip().lower() for field in fields]
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)):
            continue
        entry = dict(zip(cleaned_fields, row, strict=False))
        output.append(entry)
    return output


def _rows_from_eph(rows: list[dict[str, Any]], data: dict[str, Any], tdes: str) -> list[dict[str, Any]]:
    fields = data.get("data-fields")
    if fields is None and rows:
        fields = rows[0].get("data-fields")
    if not fields:
        return []
    data_rows = []
    for entry in rows:
        entry_rows = entry.get("data")
        if isinstance(entry_rows, list):
            data_rows = entry_rows
            break
    if not data_rows:
        return []
    return _rows_from_fields(list(fields), data_rows, tdes)


__all__ = ["ScoutClient"]
