"""Direct ASCOM Alpaca mount control -- Phase 1 of the direct_hardware branch.

Talks to the ZWO AM3's ASCOM driver via an Alpaca-compatible bridge (ASCOM
Remote Server / Alpaca Device Hub) running on the Windows host, instead of
going through NINA. Implements the same method surface as the mount-related
subset of NinaBridgeService (app/services/nina_client.py) so it's a drop-in
replacement in sequential_capture.py -- see documentation/DIRECT_HARDWARE_DESIGN.md.

NOT YET VERIFIED AGAINST REAL HARDWARE. ASCOM Remote Server / Alpaca Device
Hub isn't reachable yet (confirmed live: ports 11111/32227 both time out from
the api container, while NINA's own port 1888 is open) -- this is written
against the Alpaca specification and needs real-hardware testing once that
bridge is running.

Key protocol details that matter here and are easy to get wrong:
- Alpaca's RightAscension is in HOURS (0-24), not degrees. NINA's API (and
  everything else in this codebase) uses degrees. Converted at the boundary.
- Every Alpaca call needs ClientID + ClientTransactionID and returns an
  envelope: {"Value":..., "ErrorNumber":0, "ErrorMessage":""} for GET,
  or just {"ErrorNumber":0, "ErrorMessage":""} for PUT (no "Value").
- The mount's EquatorialSystem property tells you whether RightAscension/
  Declination are reported in JNOW (Topocentric, value=1) or J2000 (value=2).
  sequential_capture.py's mount_info() consumer always runs the result
  through _jnow_to_icrs() -- so mount_info() here normalizes to JNOW
  regardless of what the mount actually reports, to preserve that contract
  without requiring any change to sequential_capture.py. NOT YET VERIFIED
  which EquatorialSystem the AM3's driver actually reports.
"""

from __future__ import annotations

import itertools
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ASCOM DriveRates enum (Alpaca `trackingrate` property).
TRACKING_RATE_SIDEREAL = 0
TRACKING_RATE_LUNAR = 1
TRACKING_RATE_SOLAR = 2
TRACKING_RATE_KING = 3

# ASCOM EquatorialCoordinateType enum (Alpaca `equatorialsystem` property).
EQ_SYSTEM_OTHER = 0
EQ_SYSTEM_TOPOCENTRIC = 1  # JNOW -- what sequential_capture.py assumes today
EQ_SYSTEM_J2000 = 2
EQ_SYSTEM_J2050 = 3
EQ_SYSTEM_B1950 = 4

# ASCOM TelescopeAxes enum (Alpaca `moveaxis`/`axisrates`/`canmoveaxis` Axis param).
AXIS_PRIMARY = 0  # RA (equatorial) / Azimuth (alt-az)
AXIS_SECONDARY = 1  # Dec (equatorial) / Altitude (alt-az)


class AlpacaError(Exception):
    """An Alpaca device returned ErrorNumber != 0."""


class AlpacaMountClient:
    """Drop-in mount-control replacement for NinaBridgeService, talking
    directly to an Alpaca-exposed ASCOM telescope driver."""

    def __init__(
        self,
        base_url: str,
        device_number: int = 0,
        client_id: int = 1,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.device_number = device_number
        self.client_id = client_id
        self.timeout = timeout
        self._transaction_id = itertools.count(1)
        self._equatorial_system: int | None = None  # cached after first query

    def _url(self, member: str) -> str:
        return f"{self.base_url}/api/v1/telescope/{self.device_number}/{member}"

    def _request(self, method: str, member: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        params["ClientID"] = self.client_id
        params["ClientTransactionID"] = next(self._transaction_id)
        # httpx encodes Python bool as lowercase "true"/"false"; the Alpaca
        # spec (.NET heritage) requires capitalized "True"/"False". Confirmed
        # via a mock-transport test that httpx's default encoding is
        # lowercase -- send the exact spec-compliant casing rather than
        # trust a real device's parser to be lenient about it.
        params = {
            key: ("True" if value is True else "False" if value is False else value)
            for key, value in params.items()
        }

        url = self._url(member)
        try:
            if method == "GET":
                response = httpx.get(url, params=params, timeout=self.timeout)
            else:
                response = httpx.put(url, data=params, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise AlpacaError(f"Failed to reach Alpaca device at {url}: {exc}") from exc

        error_number = data.get("ErrorNumber", 0)
        if error_number:
            raise AlpacaError(
                f"Alpaca error {error_number} on {member}: {data.get('ErrorMessage')}"
            )
        return data.get("Value")

    def _get(self, member: str, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", member, params)

    def _put(self, member: str, params: dict[str, Any] | None = None) -> None:
        self._request("PUT", member, params)

    # --- Connection ---

    def connect_telescope(self, connect: bool, device_id: str | None = None) -> str:
        self._put("connected", {"Connected": connect})
        return "connected" if connect else "disconnected"

    def park_telescope(self, park: bool) -> str:
        self._put("park" if park else "unpark")
        return "parked" if park else "unparked"

    # --- Position / status ---

    def _get_equatorial_system(self) -> int:
        if self._equatorial_system is None:
            try:
                self._equatorial_system = int(self._get("equatorialsystem"))
            except Exception as exc:
                logger.warning(
                    "Could not read equatorialsystem, assuming JNOW/Topocentric: %s", exc
                )
                self._equatorial_system = EQ_SYSTEM_TOPOCENTRIC
        return self._equatorial_system

    # --- Raw axis control (bypasses GOTO/coordinate-transform logic
    # entirely -- see all_sky_polar_align.py for why this matters) ---

    def can_move_axis(self, axis: int) -> bool:
        try:
            return bool(self._get("canmoveaxis", {"Axis": axis}))
        except Exception:
            return False

    def get_axis_rates(self, axis: int) -> list[dict[str, float]]:
        """The driver's own allowed rates (deg/sec) for MoveAxis on this
        axis -- querying rather than guessing a hardcoded rate."""
        raw = self._get("axisrates", {"Axis": axis})
        return [{"Minimum": float(r["Minimum"]), "Maximum": float(r["Maximum"])} for r in (raw or [])]

    def move_axis(self, axis: int, rate_deg_per_sec: float) -> None:
        """Start (or stop, with rate 0.0) a raw axis rotation at a fixed
        rate. No coordinate solving, no meridian/pier-flip logic -- just
        spins the motor, same as a hand-paddle jog. Caller is responsible
        for stopping it (rate 0.0) and for polling position to know how
        far it's gone."""
        self._put("moveaxis", {"Axis": axis, "Rate": rate_deg_per_sec})

    def get_sidereal_time_hours(self) -> float:
        """Local apparent sidereal time in hours -- used to compute hour
        angle (HA = LST - RA) so callers can tell whether a planned RA
        slew would cross the meridian."""
        return float(self._get("siderealtime"))

    def mount_info_raw(self) -> dict[str, Any]:
        """Raw Alpaca properties. RightAscension is in HOURS here, matching
        Alpaca's convention -- mount_info() below converts to degrees."""
        return {
            "Connected": self._get("connected"),
            "RightAscension": self._get("rightascension"),
            "Declination": self._get("declination"),
            "Slewing": self._get("slewing"),
            "AtPark": self._get("atpark"),
            "Tracking": self._get("tracking"),
        }

    def mount_info(self) -> dict[str, Any]:
        """Normalized shape matching NinaBridgeService.mount_info(), so
        sequential_capture.py needs no changes. ra_deg/dec_deg are always
        JNOW (topocentric, current-epoch apparent), converting from J2000
        if that's what the mount actually reports -- NOT YET VERIFIED which
        one the AM3 driver reports; this branch assumes Topocentric/JNOW
        (the common default) until tested against real hardware.
        """
        raw = self.mount_info_raw()
        ra_deg = float(raw["RightAscension"]) * 15.0 if raw.get("RightAscension") is not None else None
        dec_deg = raw.get("Declination")

        eq_system = self._get_equatorial_system()
        if eq_system == EQ_SYSTEM_J2000 and ra_deg is not None and dec_deg is not None:
            # Mount reports J2000 -- convert to JNOW so the contract this
            # method promises (see module docstring) holds regardless.
            from astropy.coordinates import FK5, SkyCoord
            from astropy.time import Time
            import astropy.units as u

            coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
            jnow = coord.transform_to(FK5(equinox=Time.now()))
            ra_deg, dec_deg = float(jnow.ra.deg), float(jnow.dec.deg)
        elif eq_system not in (EQ_SYSTEM_TOPOCENTRIC, EQ_SYSTEM_J2000):
            logger.warning(
                "Mount reports unexpected equatorialsystem=%s; treating "
                "coordinates as JNOW without conversion -- verify against "
                "real hardware.",
                eq_system,
            )

        return {
            "is_connected": bool(raw.get("Connected", False)),
            "is_parked": bool(raw.get("AtPark", False)),
            "is_slewing": bool(raw.get("Slewing", False)),
            "is_tracking": bool(raw.get("Tracking", False)),
            "ra_deg": ra_deg,
            "dec_deg": dec_deg,
        }

    # --- Slewing ---

    def slew(self, ra_deg: float, dec_deg: float) -> str:
        """Async slew (fire-and-forget, matching NinaBridgeService.slew's
        contract -- pair with wait_for_mount_ready to block until done)."""
        self._put(
            "slewtocoordinatesasync",
            {"RightAscension": ra_deg / 15.0, "Declination": dec_deg},
        )
        return "slewing"

    def sync_mount(self, ra_deg: float | None = None, dec_deg: float | None = None) -> str:
        if ra_deg is None or dec_deg is None:
            raise ValueError(
                "AlpacaMountClient.sync_mount requires explicit ra_deg/dec_deg "
                "(no blind-solve-and-sync capability at the mount level via Alpaca)"
            )
        self._put(
            "synctocoordinates",
            {"RightAscension": ra_deg / 15.0, "Declination": dec_deg},
        )
        return "synced"

    def wait_for_mount_ready(
        self,
        timeout: float = 180.0,
        poll_interval: float = 1.0,
        settle_seconds: float = 3.0,
    ) -> None:
        """Wait for slewing to stop, then a fixed settle window -- same
        contract and transient-failure tolerance as NinaBridgeService's
        version (app/services/nina_client.py)."""
        deadline = time.time() + timeout
        settle_deadline = 0.0
        while time.time() < deadline:
            try:
                slewing = self._get("slewing")
            except Exception as exc:
                logger.warning("Mount status poll failed, will retry: %s", exc)
                time.sleep(poll_interval)
                continue
            if slewing:
                settle_deadline = 0.0
            else:
                if settle_deadline == 0.0:
                    settle_deadline = time.time() + settle_seconds
                elif time.time() >= settle_deadline:
                    return
            time.sleep(poll_interval)
        raise AlpacaError("Mount is still slewing or settling after timeout")

    # --- Tracking (the actual point of this branch) ---

    def ensure_sidereal_tracking(self) -> None:
        """Explicitly enable sidereal tracking and verify it's engaged --
        the gap identified in the NINA integration that this branch exists
        to close. Unlike NINA's plugin, Alpaca's Tracking/TrackingRate
        properties are directly readable, so this can actually confirm the
        state rather than just hoping.
        """
        try:
            can_set = self._get("cansettracking")
        except Exception:
            can_set = True  # assume yes if the capability flag itself fails
        if can_set:
            self._put("trackingrate", {"TrackingRate": TRACKING_RATE_SIDEREAL})
            self._put("tracking", {"Tracking": True})
        else:
            logger.warning(
                "Mount reports cansettracking=False; cannot explicitly "
                "enable tracking via Alpaca."
            )

        is_tracking = bool(self._get("tracking"))
        if not is_tracking:
            raise AlpacaError(
                "Tracking is not engaged after attempting to enable it -- "
                "refusing to proceed with exposures on a non-tracking mount."
            )


__all__ = ["AlpacaMountClient", "AlpacaError"]
