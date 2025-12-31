"""Imaging helpers for FITS naming and file naming."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from app.core.config import settings
from app.core.site_config import load_site_config
from zoneinfo import ZoneInfo

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
    utc_time = _as_utc(start_time)
    ts = utc_time.strftime("%Y%m%dT%H%M%SZ")
    filename = f"{safe_target}-{ts}_{seq_label}_{seq_name}.{extension.lstrip('.')}"
    date_dir = _local_date_dir(utc_time)
    root = Path(settings.data_root) / "fits" / safe_target / date_dir
    return root / filename


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _local_date_dir(value_utc: datetime) -> str:
    # FITS folders roll over at local midnight; filenames remain in UTC.
    site_config = load_site_config()
    local_tz = ZoneInfo(site_config.timezone)
    return value_utc.astimezone(local_tz).strftime("%Y/%m/%d")


__all__ = ["build_fits_path", "sanitize_target_name"]
