"""Thin wrapper around astrometry.net solve-field."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from app.core.config import settings


class SolveError(RuntimeError):
    pass


def solve_fits(
    fits_path: str | Path,
    radius_deg: float | None = None,
    ra_hint: float | None = None,
    dec_hint: float | None = None,
    downsample: int | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run solve-field and return a summary dict.

    This expects astrometry.net tools and index files to be available in PATH/ASTROMETRY_INDEX_DIR.
    """

    path = Path(fits_path)
    if not path.exists():
        raise SolveError(f"FITS not found: {path}")

    cmd = [
        "solve-field",
        "--overwrite",
        "--no-plots",
        "--json",
        str(path),
    ]
    if radius_deg is not None:
        cmd += ["--radius", str(radius_deg)]
    if ra_hint is not None and dec_hint is not None:
        cmd += ["--ra", str(ra_hint), "--dec", str(dec_hint)]
    if downsample:
        cmd += ["--downsample", str(downsample)]

    try:
        result = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        raise SolveError(exc.stderr or exc.stdout or str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise SolveError("solve-field timed out") from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SolveError(f"Failed to parse solve-field JSON: {exc}") from exc
    return data


__all__ = ["solve_fits", "SolveError"]
