"""Simple retention prune helper (dry-run by default)."""

from __future__ import annotations

import argparse
import os
from typing import Iterable

from app.services.imaging import retention_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune FITS files past retention.")
    parser.add_argument("--apply", action="store_true", help="Actually delete files (default dry-run).")
    parser.add_argument("--root", type=str, default=None, help="Override data root (default uses settings.data_root).")
    args = parser.parse_args()

    files = list(retention_candidates(root=args.root))
    if not files:
        print("No expired FITS files found.")
        return

    if not args.apply:
        print(f"[dry-run] {len(files)} expired files:")
        for path in files:
            print(path)
        return

    deleted = 0
    for path in files:
        try:
            os.remove(path)
            deleted += 1
        except OSError as exc:  # noqa: BLE001
            print(f"Failed to delete {path}: {exc}")
    print(f"Deleted {deleted}/{len(files)} expired files.")


if __name__ == "__main__":
    main()
