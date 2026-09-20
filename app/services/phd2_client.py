"""Direct PHD2 guiding control -- Phase 3 of the direct_hardware branch.

PHD2 already talks directly to the guide camera (ZWO ASI220MM Mini) and
mount (ASI Mount via ASCOM) on its own -- this is how NINA itself controls
PHD2 today, via PHD2's standard "Event Monitoring and Server API" (a plain
TCP socket, newline-delimited JSON, default port 4400). Connecting to it
directly, with no NINA in between, is a clean fit for this branch: no new
guiding logic to write, just a thin client for an existing, well-documented
protocol that PHD2 has exposed for years.

Confirmed live against the real PHD2 instance before writing this (not
just from the spec): on connect, PHD2 sends a burst of state events
(Version, CalibrationComplete, AppState); RPC calls are single-line JSON
objects {"method":..., "id":...} terminated with \\r\\n, answered with
{"jsonrpc":"2.0","result":...,"id":...} on the same connection, interleaved
with further unsolicited Event objects (no "id" field) that arrive whenever
PHD2's state changes. Verified get_connected, get_calibrated,
get_current_equipment, get_exposure, get_camera_frame_size all match the
real, already-configured equipment (guide camera + mount both connected,
calibration already present).

NOT YET VERIFIED against real guiding: it's daytime, and guiding needs a
real star -- the query/status methods above are confirmed live, but
guide_and_wait_for_settle()'s actual behavior (does a real SettleDone event
arrive, does Status=0 mean what the docs say) needs a real night test.
"""

from __future__ import annotations

import itertools
import json
import logging
import socket
import time
from typing import Any

logger = logging.getLogger(__name__)


class Phd2Error(Exception):
    """PHD2 returned a JSON-RPC error, or the connection/protocol broke."""


class Phd2Client:
    """Thin client for PHD2's Event Monitoring and Server API.

    One TCP connection, used synchronously: every public method either
    sends an RPC and waits for its matching response (dispatching any
    interleaved Events to update cached state along the way), or waits on
    cached state updated by those Events (e.g. waiting for a SettleDone).
    """

    def __init__(self, host: str, port: int = 4400, timeout: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = b""
        self._id_counter = itertools.count(1)
        self.app_state: str | None = None
        self.last_settle: dict[str, Any] | None = None
        self.last_alert: dict[str, Any] | None = None
        # Optional hook invoked for every Event received (in addition to
        # the built-in handling above) -- used by guide_telemetry.py's
        # background listener to capture GuideStep events for the live
        # guiding graph without this class needing to know anything about
        # that consumer.
        self.on_event: Any = None

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # Drain PHD2's initial event burst (Version, CalibrationComplete,
        # AppState, ...) so self.app_state reflects reality before any RPC.
        try:
            self._pump(timeout=1.0)
        except Phd2Error:
            pass

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self) -> "Phd2Client":
        self.connect()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    # --- Low-level protocol ---

    def _read_line(self, deadline: float) -> str | None:
        assert self._sock is not None
        while b"\n" not in self._buf:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            self._sock.settimeout(remaining)
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                raise Phd2Error("Connection closed by PHD2")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        text = line.decode(errors="replace").strip()
        return text or None

    def _handle_event(self, obj: dict[str, Any]) -> None:
        event = obj.get("Event")
        if event == "AppState":
            self.app_state = obj.get("State")
        elif event == "GuidingStopped":
            self.app_state = "Stopped"
        elif event == "SettleDone":
            self.last_settle = obj
        elif event == "StarLost":
            logger.warning("PHD2: StarLost (SNR=%s)", obj.get("SNR"))
        elif event == "Alert":
            self.last_alert = obj
            logger.warning("PHD2 alert: %s", obj.get("Msg"))
        if self.on_event is not None:
            try:
                self.on_event(obj)
            except Exception:
                logger.exception("Phd2Client.on_event callback raised")

    def _pump(self, timeout: float) -> None:
        """Read and dispatch events (and stray non-matching responses)
        until `timeout` elapses, without expecting any particular reply."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._read_line(deadline)
            if line is None:
                return
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "Event" in obj:
                self._handle_event(obj)

    def _call(self, method: str, params: Any = None, timeout: float | None = None) -> Any:
        if self._sock is None:
            raise Phd2Error("Not connected")
        req_id = next(self._id_counter)
        payload: dict[str, Any] = {"method": method, "id": req_id}
        if params is not None:
            payload["params"] = params
        self._sock.sendall((json.dumps(payload) + "\r\n").encode())

        deadline = time.time() + (timeout or self.timeout)
        while time.time() < deadline:
            line = self._read_line(deadline)
            if line is None:
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "Event" in obj:
                self._handle_event(obj)
                continue
            if obj.get("id") == req_id:
                if "error" in obj:
                    err = obj["error"]
                    raise Phd2Error(f"PHD2 error on {method}: {err}")
                return obj.get("result")
        raise Phd2Error(f"Timed out waiting for response to {method}")

    def wait_until(self, predicate, timeout: float) -> bool:
        """Pump events until `predicate()` is true or `timeout` elapses."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            remaining = min(1.0, deadline - time.time())
            if remaining > 0:
                self._pump(timeout=remaining)
        return predicate()

    # --- Queries ---

    def get_app_state(self) -> str:
        return self._call("get_app_state")

    def get_connected(self) -> bool:
        return bool(self._call("get_connected"))

    def get_calibrated(self) -> bool:
        return bool(self._call("get_calibrated"))

    def get_current_equipment(self) -> dict[str, Any]:
        return self._call("get_current_equipment")

    # --- Actions ---

    def set_connected(self, connected: bool) -> None:
        self._call("set_connected", [connected])

    def guide_and_wait_for_settle(
        self,
        settle_pixels: float = 1.5,
        settle_time: float = 8.0,
        settle_timeout: float = 60.0,
        recalibrate: bool = False,
    ) -> bool:
        """Start guiding (auto-selecting/recalibrating as needed) and block
        until PHD2 reports the guide star has settled. Returns True on a
        successful settle (SettleDone Status=0), False on a reported
        failure (star lost, settle timeout inside PHD2 itself, etc).

        NOT YET VERIFIED against real guiding -- see module docstring.
        """
        self.last_settle = None
        settle = {"pixels": settle_pixels, "time": settle_time, "timeout": settle_timeout}
        self._call("guide", {"settle": settle, "recalibrate": recalibrate})

        got_settle = self.wait_until(
            lambda: self.last_settle is not None, timeout=settle_timeout + 30.0
        )
        if not got_settle:
            raise Phd2Error("Timed out waiting for SettleDone after guide")

        status = self.last_settle.get("Status")
        if status == 0:
            logger.info("PHD2 guiding settled successfully")
            return True
        logger.warning(
            "PHD2 settle failed: status=%s error=%s", status, self.last_settle.get("Error")
        )
        return False

    def listen_forever(self, stop_flag) -> None:
        """Block, dispatching events via on_event, until stop_flag() is
        true or the connection breaks. Intended to run in a dedicated
        background thread -- see guide_telemetry.py."""
        while not stop_flag():
            try:
                self._pump(timeout=1.0)
            except Phd2Error:
                raise

    def stop_capture(self) -> None:
        self._call("stop_capture")
        self.wait_until(lambda: self.app_state == "Stopped", timeout=10.0)


__all__ = ["Phd2Client", "Phd2Error"]
