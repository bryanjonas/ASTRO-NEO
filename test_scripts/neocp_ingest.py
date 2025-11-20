#!/usr/bin/env python3
"""Ingest the MPC NEO Confirmation Page into Postgres."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.db.session import init_db  # noqa: E402
from app.services.neocp import refresh_neocp_candidates  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync MPC NEOCP entries into Postgres.")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use the local HTML snapshot defined in config instead of hitting MPC.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    init_db()
    results = refresh_neocp_candidates(use_local=args.local)
    print(f"Synced {len(results)} NEOCP candidates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
