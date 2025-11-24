"""Imaging helpers for FITS naming and file naming."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from app.core.config import settings

# Lowercase, replace spaces with underscores, and strip characters that would confuse filesystems.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_target_name(name: str) -> str:
    """Return a filesystem-safe target name."""
    cleaned = name.strip().replace(" ", "_")
    cleaned = _SAFE_CHARS.sub("_", cleaned)
    cleaned = cleaned.strip("._-")
    return cleaned or "target"


def build_fits_path(
    target_name: str,
    start_time: datetime,
    sequence_name: str | None = None,
    index: int | None = None,
    extension: str = "fits",
) -> Path:
    """
    Construct a FITS path using the naming convention:
    /data/fits/<target>/<YYYY>/<MM>/<DD>/<target>-<timestamp>_<seq>_<sequence>.fits
    """

    safe_target = sanitize_target_name(target_name)
    seq_label = f"{index:03d}" if index is not None else "000"
    seq_name = sanitize_target_name(sequence_name) if sequence_name else "seq"
    ts = start_time.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{safe_target}-{ts}_{seq_label}_{seq_name}.{extension.lstrip('.')}"
    root = Path(settings.data_root) / "fits" / safe_target / start_time.strftime("%Y/%m/%d")
    return root / filename


__all__ = ["build_fits_path", "sanitize_target_name"]
