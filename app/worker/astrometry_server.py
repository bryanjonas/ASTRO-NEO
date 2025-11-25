"""Lightweight ASTROMETRY worker that wraps solve-field over HTTP."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Generator

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException

from app.services.solver import SolveError, _solve_local
from app.core.config import settings

app = FastAPI(title="astrometry-worker", version="0.1.0")

logging.basicConfig(level=logging.INFO)

CONFIG_PATHS = [Path(settings.astrometry_config_path), Path("/etc/astrometry.cfg")]


def _build_config() -> None:
    """Write an astrometry.cfg that enumerates available indexes."""
    index_root = Path("/data/indexes")
    files = sorted(index_root.glob("index-*.fits"))
    if not files:
        logging.error("No astrometry index files found in %s", index_root)
        return
    lines = ["add_path /data/indexes", "inparallel 4"]
    lines += [f"index {f}" for f in files]
    content = "\n".join(lines) + "\n"
    for cfg in CONFIG_PATHS:
        try:
            cfg.write_text(content, encoding="utf-8")
            logging.info("Astrometry config written to %s with %d indexes", cfg, len(files))
            return  # Stop after writing the first successful one
        except Exception as exc:  # pragma: no cover - defensive
            logging.warning("Failed to write astrometry config %s: %s", cfg, exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> Generator[None, None, None]:
    _build_config()
    yield


@app.post("/solve")
def solve(payload: dict[str, Any]) -> dict[str, Any]:
    path = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="path_required")
    fits_path = Path(path)
    logging.info("Received solve request for %s", fits_path)
    logging.info("Params: radius=%s, hint=%s/%s, downsample=%s", 
                 payload.get("radius_deg"), payload.get("ra_hint"), payload.get("dec_hint"), payload.get("downsample"))
    
    try:
        result = _solve_local(
            fits_path,
            radius_deg=payload.get("radius_deg"),
            ra_hint=payload.get("ra_hint"),
            dec_hint=payload.get("dec_hint"),
            downsample=payload.get("downsample"),
            timeout=payload.get("timeout") or 300,
        )
        logging.info("Solve successful for %s", fits_path)
        logging.debug("Result: %s", result)
    except SolveError as exc:
        logging.error("Solve failed for %s: %s", fits_path, exc)
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:  # pragma: no cover
        logging.exception("Unhandled error solving %s", fits_path)
        raise HTTPException(status_code=500, detail=str(exc))
    return result


app.router.lifespan_context = lifespan


def run() -> None:
    uvicorn.run("app.worker.astrometry_server:app", host="0.0.0.0", port=8100, reload=False)


if __name__ == "__main__":
    run()
