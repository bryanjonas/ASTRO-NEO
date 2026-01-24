#!/usr/bin/env python3
"""Run a local solve-field test against the most recent FITS file."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

from astropy.io import fits

from app.core.config import settings
from app.services.solver import SolveError, solve_fits


def _find_latest_fits(root: Path) -> Optional[Path]:
    latest_path = None
    latest_mtime = -1.0
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            if not name.lower().endswith(".fits"):
                continue
            path = Path(dirpath) / name
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_path = path
    return latest_path


def _load_header_hints(path: Path) -> tuple[Optional[float], Optional[float]]:
    try:
        header = fits.getheader(path)
    except Exception:
        return None, None
    ra = header.get("RA") or header.get("CRVAL1")
    dec = header.get("DEC") or header.get("CRVAL2")
    try:
        ra_val = float(ra) if ra is not None else None
    except (TypeError, ValueError):
        ra_val = None
    try:
        dec_val = float(dec) if dec is not None else None
    except (TypeError, ValueError):
        dec_val = None
    return ra_val, dec_val


def main() -> int:
    parser = argparse.ArgumentParser(description="Local solve-field smoke test.")
    parser.add_argument(
        "--path",
        help="FITS path to solve (defaults to newest under /data).",
    )
    parser.add_argument(
        "--root",
        default="/data",
        help="Root directory to search for FITS files (default: /data).",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=settings.astrometry_search_radius_deg or settings.confirmation_solve_radius_deg,
        help="Search radius in degrees (default from settings).",
    )
    parser.add_argument(
        "--downsample",
        type=int,
        default=settings.confirmation_solve_downsample,
        help="Downsample factor for solve-field.",
    )
    args = parser.parse_args()

    if args.path:
        fits_path = Path(args.path)
    else:
        fits_path = _find_latest_fits(Path(args.root))
        if not fits_path:
            print(f"No FITS files found under {args.root}", file=sys.stderr)
            return 1

    if not fits_path.exists():
        print(f"FITS not found: {fits_path}", file=sys.stderr)
        return 1

    ra_hint, dec_hint = _load_header_hints(fits_path)
    print(f"Using FITS: {fits_path}")
    print(f"Header hints: RA={ra_hint} Dec={dec_hint}")
    print(f"Radius: {args.radius} deg, downsample={args.downsample}")
    print(f"Scale bounds: low={settings.confirmation_scale_low_arcsec} high={settings.confirmation_scale_high_arcsec}")

    start = time.time()
    try:
        result = solve_fits(
            fits_path,
            radius_deg=args.radius,
            ra_hint=ra_hint,
            dec_hint=dec_hint,
            downsample=args.downsample,
            sigma=settings.confirmation_solve_sigma,
            scale_low_arcsec=settings.confirmation_scale_low_arcsec,
            scale_high_arcsec=settings.confirmation_scale_high_arcsec,
        )
    except SolveError as exc:
        elapsed = time.time() - start
        print(f"Solve failed after {elapsed:.1f}s: {exc}", file=sys.stderr)
        return 2

    elapsed = time.time() - start
    solution = result.get("solution", {})
    print(f"Solve completed in {elapsed:.1f}s")
    print(
        "Solution: RA={ra_deg} Dec={dec_deg} pixscale={pixscale} orientation={orientation} epoch={epoch}".format(
            ra_deg=solution.get("ra_deg"),
            dec_deg=solution.get("dec_deg"),
            pixscale=solution.get("pixscale"),
            orientation=solution.get("orientation"),
            epoch=solution.get("epoch"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
