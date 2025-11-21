"""Quick smoke test against the NINA bridge (mock or real)."""

from __future__ import annotations

import time
from typing import Any

import httpx

BASE_URL = "http://localhost:1889/api"


def _get(client: httpx.Client, path: str) -> httpx.Response:
    return client.get(f"{BASE_URL}{path}")


def _post(client: httpx.Client, path: str, payload: dict[str, Any] | None = None) -> httpx.Response:
    return client.post(f"{BASE_URL}{path}", json=payload or {})


def _print_result(label: str, resp: httpx.Response) -> None:
    print(f"{label}: {resp.status_code} {resp.text}")


def _wait_for_ready(client: httpx.Client, key: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready = _get(client, "/status").json().get("ready") or {}
        if ready.get(key):
            return
        time.sleep(0.5)
    print(f"Timed out waiting for {key}=true")


def main() -> None:
    with httpx.Client(timeout=10) as client:
        resp = _get(client, "/status")
        _print_result("status", resp)
        data = resp.json()
        filters = (data.get("equipment_profile") or {}).get("camera", {}).get("filters") or ["L"]
        first_filter = filters[0]
        telescope = (data.get("nina_status") or {}).get("telescope") or {}
        blockers = data.get("blockers") or []
        if blockers:
            print(f"Blockers present, skipping actions: {blockers}")
            return

        if telescope.get("is_parked"):
            print("Unparking telescope")
            _print_result("unpark", _post(client, "/telescope/park", {"park": False}))

        _print_result("clear override", _post(client, "/override", {"manual_override": False}))

        print("Slew -> 10,10")
        _print_result("slew", _post(client, "/telescope/slew", {"ra_deg": 10, "dec_deg": 10}))

        print("Exposure 2s")
        _print_result(
            "expose",
            _post(
                client,
                "/camera/exposure",
                {"filter": first_filter, "binning": 1, "exposure_seconds": 2},
            ),
        )
        _wait_for_ready(client, "ready_to_expose", timeout=5)

        print("Start sequence (2x2s)")
        seq_resp = _post(
            client,
            "/sequence/start",
            {"name": "smoke", "count": 2, "filter": first_filter, "binning": 1, "exposure_seconds": 2},
        )
        _print_result("sequence start", seq_resp)
        time.sleep(1)
        _print_result("sequence status", _get(client, "/sequence/status"))

        print("Abort sequence")
        _print_result("sequence abort", _post(client, "/sequence/abort"))
        _print_result("sequence status", _get(client, "/sequence/status"))

        print("Wait for camera idle, then park telescope")
        _wait_for_ready(client, "ready_to_expose", timeout=10)
        _print_result("park", _post(client, "/telescope/park", {"park": True}))
        _print_result("status", _get(client, "/status"))


if __name__ == "__main__":
    main()
