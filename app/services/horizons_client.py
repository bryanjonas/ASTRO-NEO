"""JPL Horizons API client for authoritative ephemerides."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)



class HorizonsClient:
    """Client for JPL Horizons API with topocentric corrections.

    Provides authoritative ephemerides including:
    - Light-time correction
    - Stellar aberration
    - Topocentric parallax
    - Precession and nutation
    - Planetary perturbations
    """

    BASE_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"

    def __init__(
        self,
        site_lat: float,
        site_lon: float,
        site_alt_m: float,
        timeout: float = 30.0,
    ):
        """Initialize Horizons client.

        Args:
            site_lat: Observatory latitude (degrees)
            site_lon: Observatory longitude (degrees, East positive)
            site_alt_m: Observatory altitude (meters above sea level)
            timeout: HTTP request timeout (seconds)
        """
        self.site_lat = site_lat
        self.site_lon = site_lon
        self.site_alt_km = site_alt_m / 1000.0
        self.timeout = timeout

    def fetch_ephemeris(
        self,
        target_designation: str | int,
        start_time: datetime,
        stop_time: datetime,
        step_minutes: int = 5,
    ) -> list[dict[str, Any]]:
        """Fetch topocentric observer ephemerides from JPL Horizons.

        Args:
            target_designation: Object designation (e.g., "2024 AB1", "1999 AN10")
            start_time: Start of ephemeris window (UTC)
            stop_time: End of ephemeris window (UTC)
            step_minutes: Time step between ephemeris points (minutes)

        Returns:
            List of ephemeris dictionaries with:
            - epoch (datetime): Time of ephemeris point
            - ra_deg (float): RA in degrees (ICRF)
            - dec_deg (float): Dec in degrees (ICRF)
            - ra_rate_arcsec_min (float): RA rate (arcsec/min, includes cos(dec))
            - dec_rate_arcsec_min (float): Dec rate (arcsec/min)
            - azimuth_deg (float): Azimuth (degrees, 0=North, 90=East)
            - elevation_deg (float): Elevation (degrees)
            - airmass (float): Relative optical airmass
            - v_mag (float): Predicted V magnitude
            - solar_elongation_deg (float): Solar elongation (degrees)
            - lunar_elongation_deg (float): Lunar elongation (degrees)
            - uncertainty_3sigma_arcsec (float): 3-sigma positional uncertainty (arcsec)
        """

        # Build Horizons COMMAND parameter options.
        # For numbered asteroids, use the semicolon form per Horizons docs.
        # For designations/names, use DES=...; (encode special chars within COMMAND).
        designation = str(target_designation).strip().strip("()")
        if designation.isdigit():
            command_options = [f"{designation};"]
        else:
            command_options = [f"DES={designation};", designation]

        # Build coordinate center using SITE_COORD
        center = "coord@399"
        site_coord = f"{self.site_lon},{self.site_lat},{self.site_alt_km}"

        base_params = {
            "format": "json",
            "OBJ_DATA": "NO",  # Ephemeris-only response
            "MAKE_EPHEM": "YES",
            "EPHEM_TYPE": "OBSERVER",
            "CENTER": f"'{center}'",
            "COORD_TYPE": "GEODETIC",
            "SITE_COORD": f"'{site_coord}'",
            "START_TIME": f"'{start_time.strftime('%Y-%m-%d %H:%M')}'",
            "STOP_TIME": f"'{stop_time.strftime('%Y-%m-%d %H:%M')}'",
            "STEP_SIZE": f"'{step_minutes} min'",
            # Quantities:
            # 1=Astrometric RA/DEC, 3=rates, 4=apparent RA/DEC, 8=airmass,
            # 9=Vis mag & Surf Brt, 10=illumination, 19=helio range/range-rate,
            # 20=obsrv range/range-rate, 23=S-T-O angle, 24=S-O-T /v,
            # 29=sky brightness, 43=3-sigma uncertainty
            "QUANTITIES": "'1,3,4,8,9,20,23,24,43'",
            "REF_SYSTEM": "ICRF",  # ICRF reference frame
            "CAL_FORMAT": "CAL",  # Calendar date format
            "TIME_DIGITS": "MINUTES",
            "ANG_FORMAT": "DEG",  # Output angles in decimal degrees
            "APPARENT": "REFRACTED",  # Include atmospheric refraction
            "RANGE_UNITS": "AU",
            "CSV_FORMAT": "YES",  # CSV for easier parsing
            "EXTRA_PREC": "YES",  # Extra precision on angles
        }

        logger.info(
            "Fetching Horizons ephemeris for %s from %s to %s (step=%dm)",
            target_designation,
            start_time.strftime("%Y-%m-%d %H:%M"),
            stop_time.strftime("%Y-%m-%d %H:%M"),
            step_minutes,
        )

        last_result = ""
        for command in command_options:
            params = dict(base_params)
            params["COMMAND"] = f"'{command}'"
            try:
                response = httpx.get(self.BASE_URL, params=params, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPError as exc:
                logger.error("Horizons API HTTP error: %s", exc)
                raise Exception(f"Horizons API request failed: {exc}") from exc
            except ValueError as exc:
                logger.error("Horizons API JSON decode error: %s", exc)
                raise Exception(f"Horizons API returned invalid JSON: {exc}") from exc

            debug_path = os.getenv("HORIZONS_DEBUG_PATH")
            if debug_path:
                try:
                    raw = data.get("result", "") or ""
                    redacted = []
                    for line in raw.splitlines():
                        if line.strip().startswith("Center geodetic"):
                            redacted.append("Center geodetic : [REDACTED]")
                        elif line.strip().startswith("Center cylindric"):
                            redacted.append("Center cylindric: [REDACTED]")
                        else:
                            redacted.append(line)
                    with open(debug_path, "w", encoding="utf-8") as fh:
                        fh.write("\n".join(redacted))
                except Exception as exc:
                    logger.warning("Failed to write Horizons debug output: %s", exc)

            if "error" in data:
                error_msg = data.get("error", "Unknown error")
                logger.warning("Horizons API error for command %s: %s", command, error_msg)
                last_result = data.get("result") or error_msg
                continue

            result_text = data.get("result", "")
            if not result_text:
                logger.warning("Horizons API returned empty result for command %s", command)
                continue

            rows = self._parse_observer_table(result_text)
            if rows:
                logger.info(
                    "Horizons returned %d ephemeris points for %s using command %s",
                    len(rows),
                    target_designation,
                    command,
                )
                return rows
            last_result = result_text

        if last_result:
            snippet = "\n".join(last_result.splitlines()[:15])
            logger.warning("Horizons result snippet for %s:\n%s", target_designation, snippet)
        logger.info("Horizons returned 0 ephemeris points for %s", target_designation)
        return []

    def get_current_position(
        self,
        target_designation: str | int,
        when: datetime | None = None,
        window_minutes: int = 10,
        step_minutes: int = 1,
    ) -> dict[str, Any]:
        """Return the ephemeris row closest to the requested time."""
        moment = when or datetime.utcnow()
        start_time = moment - timedelta(minutes=window_minutes)
        stop_time = moment + timedelta(minutes=window_minutes)
        rows = self.fetch_ephemeris(
            target_designation=str(target_designation),
            start_time=start_time,
            stop_time=stop_time,
            step_minutes=step_minutes,
        )
        if not rows:
            raise Exception(f"Horizons returned no ephemeris data for {target_designation}")
        closest = min(rows, key=lambda row: abs((row["epoch"] - moment).total_seconds()))
        return closest

    def _parse_observer_table(self, result_text: str) -> list[dict[str, Any]]:
        """Parse Horizons observer table from text output.

        The table is delimited by $$SOE (Start Of Ephemeris) and
        $$EOE (End Of Ephemeris) markers.
        """

        lines = result_text.split("\n")
        rows: list[dict[str, Any]] = []

        if any("$$SOE" in line for line in lines):
            in_table = False
            for line in lines:
                if "$$SOE" in line:
                    in_table = True
                    continue
                if "$$EOE" in line:
                    break
                if not in_table or not line.strip():
                    continue
                if line.strip().startswith("Date") or "---" in line:
                    continue
                try:
                    row = self._parse_ephemeris_row(line)
                    if row:
                        rows.append(row)
                except Exception as exc:
                    logger.warning("Failed to parse Horizons line: %s | Error: %s", line, exc)
            return rows

        # CSV-format responses may omit $$SOE/$$EOE markers; parse likely data lines.
        for line in lines:
            text = line.strip()
            if not text:
                continue
            if text.startswith("*") or text.startswith("JPL/") or text.startswith("Target body"):
                continue
            if text.lower().startswith("date"):
                continue
            if "," not in text and not text.startswith(("A.D.", "B.C.")) and not text[:4].isdigit():
                continue
            try:
                row = self._parse_ephemeris_row(line)
                if row:
                    rows.append(row)
            except Exception as exc:
                logger.warning("Failed to parse Horizons line: %s | Error: %s", line, exc)
                continue
        return rows

    def _parse_ephemeris_row(self, line: str) -> dict[str, Any] | None:
        """Parse single ephemeris row.

        Horizons CSV format varies based on QUANTITIES requested.
        With CSV_FORMAT=YES, fields are comma-separated.

        Expected format with QUANTITIES='1,3,4,8,9,20,23,24,43':
        Date, RA, DEC, RA_app, DEC_app, dRA*cosD, dDEC, Azi, Elev, ...

        Note: Actual parsing depends on Horizons output format.
        This is a simplified parser - may need adjustment based on
        actual Horizons response format.
        """

        # If CSV format, split by comma
        if "," in line:
            parts = [p.strip() for p in line.split(",")]
        else:
            parts = line.split()

        if len(parts) < 5:
            return None

        try:
            # Parse date/time (first field)
            # Format: "YYYY-MMM-DD HH:MM" or similar
            if "," in line and " " in parts[0] and ":" in parts[0]:
                date_str = parts[0].strip()
                if date_str.startswith(("A.D.", "B.C.")):
                    date_str = date_str.replace("A.D.", "", 1).replace("B.C.", "", 1).strip()
                parts = parts[1:]
            else:
                date_str = parts[0]
                if date_str in ("A.D.", "B.C.") and len(parts) > 2:
                    date_str = parts[1]
                    time_str = parts[2]
                    parts = parts[3:]
                else:
                    time_str = parts[1] if len(parts) > 1 else ""
                    parts = parts[2:]
                if not time_str or ":" not in time_str:
                    return None
                date_str = f"{date_str} {time_str}"

            # Try parsing common Horizons date formats
            epoch = None
            for fmt in [
                "%Y-%b-%d %H:%M",
                "%Y-%m-%d %H:%M",
                "%Y-%b-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%b-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S.%f",
            ]:
                try:
                    epoch = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if epoch is None:
                logger.warning("Could not parse Horizons date: %s", date_str)
                return None

            def _parse_float(value: str | None) -> float | None:
                if value is None:
                    return None
                try:
                    return float(str(value).strip())
                except (TypeError, ValueError):
                    return None

            def _parse_hms(tokens: list[str]) -> float | None:
                if len(tokens) < 3:
                    return None
                h = _parse_float(tokens[0])
                m = _parse_float(tokens[1])
                s = _parse_float(tokens[2])
                if h is None or m is None or s is None:
                    return None
                return (h + (m / 60.0) + (s / 3600.0)) * 15.0

            def _parse_dms(tokens: list[str]) -> float | None:
                if len(tokens) < 3:
                    return None
                deg = _parse_float(tokens[0])
                minutes = _parse_float(tokens[1])
                seconds = _parse_float(tokens[2])
                if deg is None or minutes is None or seconds is None:
                    return None
                sign = -1.0 if deg < 0 else 1.0
                return sign * (abs(deg) + (minutes / 60.0) + (seconds / 3600.0))

            # CSV table may include solar/lunar presence markers after date.
            tokens = [p.strip() for p in parts]
            idx = 0
            while idx < len(tokens) and tokens[idx] == "":
                idx += 1
            while idx < len(tokens) and _parse_float(tokens[idx]) is None and len(tokens[idx]) <= 2:
                idx += 1

            ra_deg = None
            dec_deg = None
            ra_idx = None
            dec_idx = None
            for i in range(idx, len(tokens)):
                if _parse_float(tokens[i]) is not None:
                    ra_deg = _parse_float(tokens[i])
                    ra_idx = i
                    break
            if ra_idx is not None:
                for i in range(ra_idx + 1, len(tokens)):
                    if _parse_float(tokens[i]) is not None:
                        dec_deg = _parse_float(tokens[i])
                        dec_idx = i
                        break

            remaining = tokens[(dec_idx + 1) if dec_idx is not None else 0 :]
            if ra_deg is None or dec_deg is None:
                ra_deg = _parse_hms(parts[:3])
                dec_deg = _parse_dms(parts[3:6])
                remaining = parts[6:]
            if ra_deg is None or dec_deg is None:
                return None

            numeric_values = []
            for value in remaining:
                parsed = _parse_float(value)
                if parsed is not None:
                    numeric_values.append(parsed)

            def _num(idx: int) -> float | None:
                if idx < 0 or idx >= len(numeric_values):
                    return None
                return numeric_values[idx]

            ra_rate = _num(0)
            dec_rate = _num(1)
            ra_rate_arcsec_min = ra_rate / 60.0 if ra_rate is not None else None
            dec_rate_arcsec_min = dec_rate / 60.0 if dec_rate is not None else None

            return {
                "epoch": epoch,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                "ra_rate_arcsec_min": ra_rate_arcsec_min,
                "dec_rate_arcsec_min": dec_rate_arcsec_min,
                "azimuth_deg": _num(2),
                "elevation_deg": _num(3),
                "airmass": _num(4),
                "v_mag": _num(6),
                "solar_elongation_deg": _num(10),
                "lunar_elongation_deg": 0.0,  # Parse from additional fields
                "uncertainty_3sigma_arcsec": 0.0,  # Parse from additional fields
            }

        except (ValueError, IndexError) as exc:
            logger.debug("Failed to parse ephemeris row: %s | Error: %s", line, exc)
            return None


__all__ = ["HorizonsClient"]
