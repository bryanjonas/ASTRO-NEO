#!/usr/bin/env python3
"""Simple helper to exercise the MPC NEOCP observations API."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx


def fetch_observations(trksub: str, formats: list[str], ades_version: str) -> list[dict[str, Any]]:
    payload = {
        "trksubs": [trksub],
        "output_format": formats,
        "ades_version": ades_version,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.request(
            "GET",
            "https://data.minorplanetcenter.net/api/get-obs-neocp",
            json=payload,
        )
        response.raise_for_status()

    data = response.json()
    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected response root: {type(data)}")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the MPC NEOCP observations API.")
    parser.add_argument("trksub", help="Tracklet ID currently on the NEOCP")
    parser.add_argument(
        "--format",
        "-f",
        action="append",
        default=["ADES_DF"],
        help="Output format to request (default: ADES_DF). Repeat for multiple formats.",
    )
    parser.add_argument(
        "--ades-version",
        default="2022",
        help="ADES version to request (default: 2022).",
    )
    args = parser.parse_args()

    payload = fetch_observations(args.trksub, args.format, args.ades_version)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
