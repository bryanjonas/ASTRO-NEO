#!/usr/bin/env python3

"""
Fetch MPC WhatsUp targets using local site config and probe Scout for each designation.

Notes:
- Uses site coordinates from config/site_local.yml or .env (via load_site_config).
- Does not print latitude/longitude to avoid leaking sensitive info.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.site_config import load_site_config
from app.services.scout_client import ScoutClient

URL = "https://minorplanetcenter.net/whatsup/index"

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


def _parse_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fetch_whatsup(payload: dict[str, Any]) -> list[dict[str, Any]]:
    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(URL, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    csrf_input = soup.find("input", {"name": "authenticity_token"})
    if not csrf_input:
        raise RuntimeError("CSRF token not found on WhatsUp page")

    payload = dict(payload)
    payload["authenticity_token"] = csrf_input["value"]
    payload.setdefault("utf8", "✓")

    response = session.post(URL, data=payload, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise RuntimeError("Results table not found in WhatsUp response")

    results_table = tables[-1]
    rows = results_table.find_all("tr")
    objects: list[dict[str, Any]] = []

    for tr in rows:
        values = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(values) != len(COLUMN_SCHEMA):
            continue
        row = dict(zip(COLUMN_SCHEMA, values))
        objects.append(
            {
                "designation": row["designation"],
                "mag": _parse_float(row["mag"]),
                "solar_elong": _parse_float(row["solar_elong"]),
                "lunar_elong": _parse_float(row["lunar_elong"]),
                "begin": {
                    "time": row["begin_time"],
                    "ra": row["begin_ra"],
                    "dec": row["begin_dec"],
                    "alt": _parse_float(row["begin_alt"]),
                },
                "max": {
                    "time": row["max_time"],
                    "ra": row["max_ra"],
                    "dec": row["max_dec"],
                    "alt": _parse_float(row["max_alt"]),
                },
                "end": {
                    "time": row["end_time"],
                    "ra": row["end_ra"],
                    "dec": row["end_dec"],
                    "alt": _parse_float(row["end_alt"]),
                },
            }
        )

    return objects


def _build_payload(
    latitude: float,
    longitude: float,
    observed_at: datetime,
    duration_hours: float,
    max_objects: int,
    min_alt: int,
    solar_elong: int,
    lunar_elong: int,
    object_type: str,
) -> dict[str, Any]:
    return {
        "utf8": "✓",
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "year": f"{observed_at.year:04d}",
        "month": f"{observed_at.month:02d}",
        "day": f"{observed_at.day:02d}",
        "hour": f"{observed_at.hour:02d}",
        "minute": f"{observed_at.minute:02d}",
        "duration": f"{duration_hours:g}",
        "max_objects": str(max_objects),
        "min_alt": str(min_alt),
        "solar_elong": str(solar_elong),
        "lunar_elong": str(lunar_elong),
        "object_type": object_type,
        "submit": "Submit",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch MPC WhatsUp targets and probe Scout for each designation."
    )
    parser.add_argument("--max-objects", type=int, default=15)
    parser.add_argument("--min-alt", type=int, default=30)
    parser.add_argument("--solar-elong", type=int, default=45)
    parser.add_argument("--lunar-elong", type=int, default=20)
    parser.add_argument("--duration-hours", type=float, default=1.0)
    parser.add_argument("--object-type", default="mp", choices=["mp", "neo", "cmt"])
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on targets to test.")
    args = parser.parse_args()

    site_config = load_site_config()
    observed_at = datetime.now(timezone.utc)
    payload = _build_payload(
        latitude=site_config.latitude,
        longitude=site_config.longitude,
        observed_at=observed_at,
        duration_hours=args.duration_hours,
        max_objects=args.max_objects,
        min_alt=args.min_alt,
        solar_elong=args.solar_elong,
        lunar_elong=args.lunar_elong,
        object_type=args.object_type,
    )

    targets = _fetch_whatsup(payload)
    if args.limit and args.limit > 0:
        targets = targets[: args.limit]

    scout = ScoutClient(obs_code=site_config.station_code)

    ok_count = 0
    fail_count = 0

    print(f"WhatsUp returned {len(targets)} targets.")
    for target in targets:
        designation = target["designation"]
        try:
            ephemeris = scout.get_current_position(designation)
            ok_count += 1
            vmag = ephemeris.get("v_mag")
            vmag_text = f"{vmag:.2f}" if isinstance(vmag, (int, float)) else "n/a"
            print(f"{designation:>12} | scout=ok | vmag={vmag_text}")
        except Exception as exc:
            fail_count += 1
            print(f"{designation:>12} | scout=fail | error={exc}")

    print(f"Scout success: {ok_count} | Scout failed: {fail_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
