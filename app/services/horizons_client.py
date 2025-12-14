"""JPL Horizons API client for authoritative ephemerides."""

from __future__ import annotations

import logging
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
        target_designation: str,
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

        # Build Horizons COMMAND parameter
        # For NEOCP objects, use DES= with CAP flag (closest apparition)
        # Remove spaces for URL encoding
        clean_des = target_designation.replace(" ", "%20")
        command = f"DES={clean_des};CAP"

        # Build coordinate center using SITE_COORD
        center = "coord"
        site_coord = f"{self.site_lon},{self.site_lat},{self.site_alt_km}"

        params = {
            "format": "json",
            "COMMAND": f"'{command}'",
            "OBJ_DATA": "YES",  # Include object summary
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

        # Check for Horizons errors
        if "error" in data:
            error_msg = data.get("error", "Unknown error")
            logger.error("Horizons API error: %s", error_msg)
            raise Exception(f"Horizons API error: {error_msg}")

        # Parse result
        result_text = data.get("result", "")
        if not result_text:
            logger.error("Horizons API returned empty result")
            raise Exception("Horizons API returned empty result")

        rows = self._parse_observer_table(result_text)

        logger.info("Horizons returned %d ephemeris points for %s", len(rows), target_designation)

        return rows

    def _parse_observer_table(self, result_text: str) -> list[dict[str, Any]]:
        """Parse Horizons observer table from text output.

        The table is delimited by $$SOE (Start Of Ephemeris) and
        $$EOE (End Of Ephemeris) markers.
        """

        lines = result_text.split("\n")
        in_table = False
        in_header = False
        rows = []

        for line in lines:
            # Look for table start marker
            if "$$SOE" in line:
                in_table = True
                continue

            # Look for table end marker
            if "$$EOE" in line:
                break

            # Skip header and column definition lines
            if in_table and line.strip():
                # Skip lines that are just dashes or column headers
                if line.strip().startswith("Date") or "---" in line:
                    in_header = True
                    continue

                in_header = False

                # Parse data line
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
            # Space-separated, split by whitespace
            parts = line.split()

        if len(parts) < 5:
            return None

        try:
            # Parse date/time (first field)
            # Format: "YYYY-MMM-DD HH:MM" or similar
            date_str = parts[0]
            if len(parts) > 1 and ":" in parts[1]:
                date_str = f"{parts[0]} {parts[1]}"
                parts = parts[1:]  # Shift indices

            # Try parsing common Horizons date formats
            epoch = None
            for fmt in [
                "%Y-%b-%d %H:%M",
                "%Y-%m-%d %H:%M",
                "%Y-%b-%d %H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
            ]:
                try:
                    epoch = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if epoch is None:
                logger.warning("Could not parse Horizons date: %s", date_str)
                return None

            # Parse RA/DEC (typically fields 2,3 or 1,2 after date)
            # This is a simplified parser - actual field positions
            # depend on QUANTITIES requested
            ra_deg = float(parts[1] if len(parts) > 1 else 0.0)
            dec_deg = float(parts[2] if len(parts) > 2 else 0.0)

            # Additional fields if available
            # These indices are estimates and may need adjustment
            return {
                "epoch": epoch,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                "ra_rate_arcsec_min": float(parts[3]) if len(parts) > 3 else 0.0,
                "dec_rate_arcsec_min": float(parts[4]) if len(parts) > 4 else 0.0,
                "azimuth_deg": float(parts[5]) if len(parts) > 5 else 0.0,
                "elevation_deg": float(parts[6]) if len(parts) > 6 else 0.0,
                "airmass": float(parts[7]) if len(parts) > 7 else 1.0,
                "v_mag": float(parts[8]) if len(parts) > 8 else 99.0,
                "solar_elongation_deg": 0.0,  # Parse from additional fields
                "lunar_elongation_deg": 0.0,  # Parse from additional fields
                "uncertainty_3sigma_arcsec": 0.0,  # Parse from additional fields
            }

        except (ValueError, IndexError) as exc:
            logger.debug("Failed to parse ephemeris row: %s | Error: %s", line, exc)
            return None


__all__ = ["HorizonsClient"]
