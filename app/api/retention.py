"""Retention management endpoints for FITS data."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from app.services.imaging import retention_candidates

router = APIRouter(prefix="/retention", tags=["retention"])


@router.get("/status")
def retention_status() -> Any:
    """Return a summary of files older than the retention window."""
    files = list(retention_candidates())
    return {"expired_count": len(files)}


class RetentionPrunePayload(dict):
    dry_run: bool = True


@router.post("/prune")
def retention_prune(payload: dict | None = None) -> Any:
    """List or delete files past retention. Defaults to dry-run."""
    dry_run = True
    if payload and isinstance(payload, dict):
        dry_run = bool(payload.get("dry_run", True))
    files = list(retention_candidates())
    deleted = []
    if not dry_run:
        for path in files:
            try:
                os.remove(path)
                deleted.append(str(path))
            except OSError:
                continue
    return {
        "dry_run": dry_run,
        "expired_count": len(files),
        "deleted": deleted if not dry_run else [],
        "files": [str(p) for p in files] if dry_run else [],
    }


__all__ = ["router"]
