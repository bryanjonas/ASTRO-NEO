#!/usr/bin/env python3
"""Fetch sample NEOCP observations from the MPC API.

Usage examples
--------------

Fetch ADES dataframe rows for a single trksub:

    python scripts/neocp_sample_fetch.py --trksub P11MZt2

Fetch both ADES and OBS80 text, writing the payload to disk:

    python scripts/neocp_sample_fetch.py --trksub P11MZt2 \\
        --output-formats ADES_DF OBS80 --output-file sample.json

Harvest every current trksub and store JSON files (default directory `samples/neocp`):

    python scripts/neocp_sample_fetch.py --fetch-all --output-formats ADES_DF OBS80

This script does not require credentials; it merely codifies the
`get-obs-neocp` POST workflow so we can archive historical observations and
seed local caches while developing offline functionality.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import List

import requests
from bs4 import BeautifulSoup

API_URL = "https://data.minorplanetcenter.net/api/get-obs-neocp"
NEOCP_HTML_URL = "https://minorplanetcenter.net/iau/NEO/ToConfirm.html"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch observations for a single trksub from MPC's NEOCP API."
    )
    parser.add_argument(
        "--trksub",
        help="Tracking subset identifier (e.g., P11MZt2) to query; leave blank to only list current trksubs",
    )
    parser.add_argument(
        "--output-formats",
        nargs="+",
        default=["ADES_DF"],
        help="One or more output formats (XML, ADES_DF, OBS_DF, OBS80)",
    )
    parser.add_argument(
        "--ades-version",
        choices=["2017", "2022"],
        default="2022",
        help="ADES schema version to request",
    )
    parser.add_argument(
        "--output-file",
        type=Path,
        help="Optional path to write the JSON response (defaults to stdout only)",
    )
    parser.add_argument(
        "--list-trksubs",
        action="store_true",
        help="Only list currently published trksubs from the MPC ToConfirm table",
    )
    parser.add_argument(
        "--fetch-all",
        action="store_true",
        help="Iterate over every current trksub, fetch observations, and save JSON files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("samples/neocp"),
        help="Directory used when --fetch-all is specified (default: samples/neocp)",
    )
    return parser.parse_args()


def fetch_trksubs_list() -> List[str]:
    """Scrape the MPC ToConfirm page for the current trksub IDs."""

    response = requests.get(NEOCP_HTML_URL, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    trksubs: List[str] = []

    # The current page lists objects inside <pre> blocks with checkboxes of
    # the form <input name="obj" value="P11MZt2">, so extract each value.
    for checkbox in soup.select("pre input[name='obj']"):
        trk = checkbox.get("value", "").strip()
        if trk:
            trksubs.append(trk)

    if not trksubs:
        raise RuntimeError("No trksubs found on the MPC ToConfirm page")
    return trksubs


def fetch_observations(trksub: str, formats: List[str], ades_version: str) -> dict:
    payload = {
        "trksubs": [trksub],
        "output_format": formats,
        "ades_version": ades_version,
    }
    response = requests.get(API_URL, json=payload, timeout=30)
    if not response.ok:
        raise RuntimeError(
            f"API error {response.status_code}: {response.text.strip()}"
        )
    data = response.json()
    if not data:
        raise RuntimeError("API returned an empty response list")
    return data[0]


def main() -> int:
    args = parse_args()
    try:
        trksubs = fetch_trksubs_list()
    except Exception as exc:  # noqa: BLE001 - scrape failures should surface
        print(f"Error fetching current trksub list: {exc}", file=sys.stderr)
        return 1

    if args.list_trksubs:
        print("Current trksubs from MPC:")
        for trk in trksubs:
            print(f"  {trk}")
        return 0

    if args.fetch_all:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        failures: List[str] = []
        for trk in trksubs:
            try:
                result = fetch_observations(trk, args.output_formats, args.ades_version)
            except Exception as exc:  # noqa: BLE001
                print(f"Failed to fetch {trk}: {exc}", file=sys.stderr)
                failures.append(trk)
                continue
            serialized = json.dumps(result, indent=2, sort_keys=True)
            out_path = args.output_dir / f"{trk}_{timestamp}.json"
            out_path.write_text(serialized)
            print(f"Saved {trk} payload to {out_path}")

        if failures:
            print(f"Completed with {len(failures)} failures: {', '.join(failures)}")
            return 1
        print("All trksubs fetched successfully.")
        return 0

    if not args.trksub:
        print("Current trksubs from MPC:")
        for trk in trksubs:
            print(f"  {trk}")
        print("\nRe-run with --trksub <value> to fetch a specific observation payload.")
        return 0

    if args.trksub not in trksubs:
        print(
            "Warning: provided trksub not in latest MPC list. Attempting fetch anyway...",
            file=sys.stderr,
        )
    try:
        result = fetch_observations(args.trksub, args.output_formats, args.ades_version)
    except Exception as exc:  # noqa: BLE001 - surface API failures directly
        print(f"Error fetching observations: {exc}", file=sys.stderr)
        return 1

    serialized = json.dumps(result, indent=2, sort_keys=True)
    print(serialized)

    if args.output_file:
        args.output_file.write_text(serialized)
        print(f"Saved response to {args.output_file}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
