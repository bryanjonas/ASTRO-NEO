"""Imaging helpers for FITS naming and retention calculations."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator

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


def retention_candidates(root: str | Path | None = None, retention_days: int | None = None) -> Iterator[Path]:
    """
    Yield FITS files older than the retention window.
    Caller decides whether to delete; this function is read-only.
    """

    base = Path(root or settings.data_root) / "fits"
    window_days = retention_days if retention_days is not None else settings.fits_retention_days
    cutoff = datetime.utcnow() - timedelta(days=max(0, window_days))
    if not base.exists():
        return iter(())

    def _iter_files(paths: Iterable[Path]) -> Iterator[Path]:
        for path in paths:
            if not path.is_file():
                continue
            mtime = datetime.utcfromtimestamp(path.stat().st_mtime)
            if mtime < cutoff:
                yield path

    return _iter_files(base.rglob("*.fits"))


__all__ = ["build_fits_path", "sanitize_target_name", "retention_candidates"]
