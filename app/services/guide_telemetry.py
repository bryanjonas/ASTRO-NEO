"""Background PHD2 telemetry listener for the live guiding graph.

Phd2Client's normal usage (start_guiding_best_effort, etc.) is
connect-do-one-thing-disconnect, which never sees GuideStep events since
those only arrive while a connection is open and guiding is actually
running. A live guiding graph needs a persistent connection independent
of any single capture's guide_and_wait_for_settle() call -- this module
owns that: one background thread, running for the app's whole lifetime,
maintaining a PHD2 connection whenever settings.guiding_backend == "phd2"
and buffering recent GuideStep telemetry for the API to serve.

Idle (checks the config every 10s, makes no PHD2 connection at all) when
guiding_backend isn't "phd2" -- matches this branch's pattern elsewhere
of only touching a subsystem's real backend when actually configured to.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

_HISTORY: deque[dict[str, Any]] = deque(maxlen=300)
_LOCK = threading.Lock()
_STOP = threading.Event()


def _on_event(obj: dict[str, Any]) -> None:
    if obj.get("Event") != "GuideStep":
        return
    with _LOCK:
        _HISTORY.append(
            {
                "time": obj.get("Time"),
                "ra_distance_raw": obj.get("RADistanceRaw"),
                "dec_distance_raw": obj.get("DECDistanceRaw"),
                "ra_duration": obj.get("RADuration"),
                "dec_duration": obj.get("DECDuration"),
                "snr": obj.get("SNR"),
                "star_mass": obj.get("StarMass"),
            }
        )


def get_guide_history() -> list[dict[str, Any]]:
    with _LOCK:
        return list(_HISTORY)


def _run_loop() -> None:
    from app.core.config import settings
    from app.services.phd2_client import Phd2Client, Phd2Error

    while not _STOP.is_set():
        if settings.guiding_backend != "phd2":
            _STOP.wait(10.0)
            continue
        try:
            client = Phd2Client(host=settings.phd2_host, port=settings.phd2_port, timeout=10.0)
            client.on_event = _on_event
            client.connect()
            logger.info("Guide telemetry listener connected to PHD2")
            client.listen_forever(lambda: _STOP.is_set() or settings.guiding_backend != "phd2")
            client.close()
        except Phd2Error as exc:
            logger.warning("Guide telemetry listener lost PHD2 connection, retrying: %s", exc)
            _STOP.wait(5.0)
        except Exception as exc:
            logger.warning("Guide telemetry listener error, retrying: %s", exc)
            _STOP.wait(5.0)


def start_guide_telemetry_thread() -> threading.Thread:
    thread = threading.Thread(target=_run_loop, daemon=True, name="phd2-telemetry")
    thread.start()
    return thread


__all__ = ["start_guide_telemetry_thread", "get_guide_history"]
