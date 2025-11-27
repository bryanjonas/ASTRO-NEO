"""Remote weather provider integration."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.core.site_config import SiteFileConfig, WeatherSensorConfig, load_site_config
from app.models import WeatherSnapshot

logger = logging.getLogger(__name__)


@dataclass
class WeatherSummary:
    """Normalized snapshot rolled up for quick checks."""

    fetched_at: datetime
    temperature_c: float | None = None
    wind_speed_mps: float | None = None
    relative_humidity_pct: float | None = None
    precipitation_probability_pct: float | None = None
    precipitation_mm: float | None = None
    cloud_cover_pct: float | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def is_safe(self) -> bool:
        return not self.reasons


class WeatherService:
    """Fetch and cache weather data from remote providers."""

    def __init__(self, session: Session, site_config: SiteFileConfig | None = None) -> None:
        self.session = session
        self.site_config = site_config or load_site_config()
        self.sensor = self._select_sensor()
        self.ttl = timedelta(minutes=max(1, settings.weather_snapshot_ttl_minutes))
        self.timeout = settings.weather_api_timeout

    def _select_sensor(self) -> WeatherSensorConfig | None:
        if not self.site_config.weather_sensors:
            return None
        for sensor in self.site_config.weather_sensors:
            if sensor.type and sensor.type.lower() in {"open-meteo"}:
                return sensor
        return self.site_config.weather_sensors[0]

    def get_status(self, force_refresh: bool = False) -> WeatherSummary | None:
        """Return a cached weather summary, refreshing if stale."""

        if not self.sensor:
            return None

        snapshot = self._latest_snapshot()
        if force_refresh or not snapshot or self._is_stale(snapshot):
            snapshot = self._fetch_snapshot()
        if not snapshot:
            return None
        return self._to_summary(snapshot)

    def _latest_snapshot(self) -> WeatherSnapshot | None:
        stmt = (
            select(WeatherSnapshot)
            .where(WeatherSnapshot.sensor_name == self.sensor.name)
            .order_by(WeatherSnapshot.fetched_at.desc())
        )
        return self.session.exec(stmt).first()

    def _is_stale(self, snapshot: WeatherSnapshot) -> bool:
        return datetime.utcnow() - snapshot.fetched_at > self.ttl

    def _fetch_snapshot(self) -> WeatherSnapshot | None:
        provider = (self.sensor.type or "").lower()
        if provider == "open-meteo":
            payload, metrics = self._fetch_open_meteo(self.sensor)
        else:
            logger.warning("Unsupported weather provider type: %s", provider or "unknown")
            return None

        if not payload:
            return None

        row = WeatherSnapshot(
            provider=provider or "unknown",
            sensor_name=self.sensor.name,
            endpoint=metrics.get("endpoint") or self.sensor.endpoint or "",
            fetched_at=datetime.utcnow(),
            temperature_c=metrics.get("temperature_c"),
            wind_speed_mps=metrics.get("wind_speed_mps"),
            relative_humidity_pct=metrics.get("relative_humidity_pct"),
            precipitation_probability_pct=metrics.get("precipitation_probability_pct"),
            precipitation_mm=metrics.get("precipitation_mm"),
            cloud_cover_pct=metrics.get("cloud_cover_pct"),
            payload=json.dumps(payload),
        )
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row

    def _fetch_open_meteo(
        self, sensor: WeatherSensorConfig
    ) -> tuple[dict[str, Any] | None, dict[str, float | str | None]]:
        url = sensor.endpoint or self._build_open_meteo_url()
        try:
            logger.debug("Fetching Open-Meteo weather from: %s", url)
            response = httpx.get(url, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch Open-Meteo weather (%s): %s", url, exc, exc_info=True)
            if hasattr(exc, "response") and exc.response:
                logger.warning("Open-Meteo error response: %s %s", exc.response.status_code, exc.response.text[:500])
            return None, {}

        payload = response.json()
        metrics = self._parse_open_meteo(payload)
        metrics["endpoint"] = url
        logger.debug("Successfully fetched Open-Meteo weather")
        return payload, metrics

    def _build_open_meteo_url(self) -> str:
        params = {
            "latitude": self.site_config.latitude,
            "longitude": self.site_config.longitude,
            "current": "temperature_2m,wind_speed_10m",
            "hourly": ",".join(
                [
                    "temperature_2m",
                    "relative_humidity_2m",
                    "wind_speed_10m",
                    "precipitation",
                    "precipitation_probability",
                    "cloud_cover",
                ]
            ),
            "windspeed_unit": "ms",
            "precipitation_unit": "mm",
            "timezone": "UTC",
        }
        return f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"

    def _parse_open_meteo(self, payload: dict[str, Any]) -> dict[str, float | None]:
        current = payload.get("current") or {}
        hourly = payload.get("hourly") or {}
        hourly_times = hourly.get("time") or []
        current_time = current.get("time")
        target_idx = 0
        if isinstance(hourly_times, list) and current_time in hourly_times:
            target_idx = hourly_times.index(current_time)

        hourly_units = payload.get("hourly_units") or {}
        current_units = payload.get("current_units") or {}

        temperature_unit = current_units.get("temperature_2m") or hourly_units.get("temperature_2m")
        humidity_unit = hourly_units.get("relative_humidity_2m") or "%"
        wind_unit = current_units.get("wind_speed_10m") or hourly_units.get("wind_speed_10m")
        precip_unit = hourly_units.get("precipitation") or "mm"
        precip_prob_unit = hourly_units.get("precipitation_probability") or "%"
        cloud_unit = hourly_units.get("cloud_cover") or "%"

        temperature_c = self._convert_temperature(
            self._preferred_value(
                current.get("temperature_2m"),
                self._series_value(hourly.get("temperature_2m"), target_idx),
            ),
            temperature_unit,
        )
        wind_speed_mps = self._convert_wind_speed(
            self._preferred_value(
                current.get("wind_speed_10m"),
                self._series_value(hourly.get("wind_speed_10m"), target_idx),
            ),
            wind_unit,
        )
        relative_humidity_pct = self._convert_percentage(
            self._series_value(hourly.get("relative_humidity_2m"), target_idx),
            humidity_unit,
        )
        precipitation_mm = self._convert_precipitation(
            self._series_value(hourly.get("precipitation"), target_idx),
            precip_unit,
        )
        precipitation_probability_pct = self._convert_percentage(
            self._series_value(hourly.get("precipitation_probability"), target_idx),
            precip_prob_unit,
        )
        cloud_cover_pct = self._convert_percentage(
            self._series_value(hourly.get("cloud_cover"), target_idx),
            cloud_unit,
        )

        return {
            "temperature_c": temperature_c,
            "wind_speed_mps": wind_speed_mps,
            "relative_humidity_pct": relative_humidity_pct,
            "precipitation_mm": precipitation_mm,
            "precipitation_probability_pct": precipitation_probability_pct,
            "cloud_cover_pct": cloud_cover_pct,
        }

    def _series_value(self, series: Any, index: int) -> float | None:
        if not isinstance(series, list) or index >= len(series):
            return None
        return self._coerce_float(series[index])

    def _preferred_value(self, first: Any, fallback: Any) -> float | None:
        value = self._coerce_float(first)
        if value is not None:
            return value
        return self._coerce_float(fallback)

    def _coerce_float(self, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _convert_temperature(self, value: float | None, unit: str | None) -> float | None:
        if value is None:
            return None
        unit_key = (unit or "").lower()
        if unit_key in {"c", "°c", "celsius"}:
            return value
        if unit_key in {"f", "°f", "fahrenheit"}:
            return (value - 32.0) * 5.0 / 9.0
        return value

    def _convert_wind_speed(self, value: float | None, unit: str | None) -> float | None:
        if value is None:
            return None
        unit_key = (unit or "").lower().replace(" ", "")
        if unit_key in {"m/s", "mps"}:
            return value
        if unit_key in {"km/h", "kmh"}:
            return value / 3.6
        if unit_key in {"mph"}:
            return value * 0.44704
        if unit_key in {"kn", "kt", "knot", "knots"}:
            return value * 0.514444
        return value

    def _convert_precipitation(self, value: float | None, unit: str | None) -> float | None:
        if value is None:
            return None
        unit_key = (unit or "").lower()
        if unit_key in {"mm"}:
            return value
        if unit_key in {"inch", "in"}:
            return value * 25.4
        return value

    def _convert_percentage(self, value: float | None, unit: str | None) -> float | None:
        if value is None:
            return None
        unit_key = (unit or "").lower().strip()
        if unit_key in {"%", "percent"}:
            return value
        return value

    def _to_summary(self, snapshot: WeatherSnapshot) -> WeatherSummary:
        reasons: list[str] = []
        if (
            snapshot.wind_speed_mps is not None
            and snapshot.wind_speed_mps > settings.weather_max_wind_speed_mps
        ):
            reasons.append("weather_wind")
        if (
            snapshot.precipitation_mm is not None
            and snapshot.precipitation_mm >= settings.weather_precip_block_threshold_mm
        ):
            reasons.append("weather_precip")
        if (
            snapshot.precipitation_probability_pct is not None
            and snapshot.precipitation_probability_pct >= settings.weather_max_precip_probability_pct
        ):
            reasons.append("weather_precip_chance")
        if (
            snapshot.relative_humidity_pct is not None
            and snapshot.relative_humidity_pct >= settings.weather_max_relative_humidity_pct
        ):
            reasons.append("weather_humidity")
        if (
            snapshot.cloud_cover_pct is not None
            and snapshot.cloud_cover_pct >= settings.weather_max_cloud_cover_pct
        ):
            reasons.append("weather_clouds")

        return WeatherSummary(
            fetched_at=snapshot.fetched_at,
            temperature_c=snapshot.temperature_c,
            wind_speed_mps=snapshot.wind_speed_mps,
            relative_humidity_pct=snapshot.relative_humidity_pct,
            precipitation_probability_pct=snapshot.precipitation_probability_pct,
            precipitation_mm=snapshot.precipitation_mm,
            cloud_cover_pct=snapshot.cloud_cover_pct,
            reasons=reasons,
        )


__all__ = ["WeatherService", "WeatherSummary"]
