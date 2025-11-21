"""Mock NINA FastAPI service."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .config import settings
from .fits_utils import create_dummy_fits
from .models import (
    ConnectionToggle,
    CameraExposureRequest,
    CameraStatusResponse,
    FocuserMoveRequest,
    ParkRequest,
    SequenceStartRequest,
    SequenceStatusResponse,
    SlewRequest,
    StatusResponse,
)
from .state import STATE, TelescopeState

logger = logging.getLogger("mock_nina")
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title="Mock NINA", version="0.1.0")
API_PREFIX = "/api"

state_lock = asyncio.Lock()


async def log_middleware(request: Request, call_next: Callable[[Request], Awaitable[Any]]):
    logger.info("%s %s", request.method, request.url.path)
    response = await call_next(request)
    logger.info("%s %s -> %s", request.method, request.url.path, response.status_code)
    return response


app.middleware("http")(log_middleware)


def _snapshot_state() -> StatusResponse:
    return StatusResponse(
        telescope=STATE.telescope.model_dump(),
        camera=STATE.camera.model_dump(),
        sequence=STATE.sequence.model_dump(),
        focuser=STATE.focuser.model_dump(),
    )


def _current_utc() -> datetime:
    return datetime.now(timezone.utc)


class ExposureRunningError(Exception):
    """Raised when a new exposure is requested but one is already active."""


class SequenceRunningError(Exception):
    """Raised when manual exposures are attempted during a running sequence."""


class TelescopeNotReadyError(Exception):
    """Raised when attempting an action while telescope is disconnected or parked."""


async def _start_exposure(
    duration: float,
    filter_name: str,
    binning: int,
    from_sequence: bool = False,
) -> datetime:
    async with state_lock:
        if not STATE.telescope.is_connected:
            raise TelescopeNotReadyError
        if STATE.telescope.is_parked:
            raise TelescopeNotReadyError
        if STATE.camera.is_exposing:
            raise ExposureRunningError
        if STATE.sequence.is_running and not from_sequence:
            raise SequenceRunningError
        now = _current_utc()
        camera = STATE.camera
        camera.is_exposing = True
        camera.last_status = "exposing"
        camera.last_exposure_start = now
        camera.last_exposure_duration = duration
        camera.last_image_path = None
    logger.info(
        "Exposure started: duration=%ss filter=%s binning=%s (sequence=%s)",
        duration,
        filter_name,
        binning,
        from_sequence,
    )
    return now


async def _complete_exposure(
    duration: float,
    filter_name: str,
    binning: int,
    filename: str | None = None,
    force_fail: bool = False,
) -> None:
    await asyncio.sleep(duration)
    fail = force_fail or (random.random() < settings.fail_rate)
    path = None
    status = "failed"
    if not fail:
        path = Path(filename) if filename else await STATE.next_image_path()
        create_dummy_fits(path, duration, filter_name, binning)
        status = "complete"
    async with state_lock:
        camera = STATE.camera
        camera.is_exposing = False
        camera.last_status = status
        camera.last_image_path = path
    logger.info("Exposure %s", status)


async def _perform_exposure(
    duration: float,
    filter_name: str,
    binning: int,
    *,
    filename: str | None = None,
    force_fail: bool = False,
    from_sequence: bool = False,
    background: bool = False,
) -> datetime:
    start_time = await _start_exposure(duration, filter_name, binning, from_sequence)

    async def runner() -> None:
        try:
            await _complete_exposure(duration, filter_name, binning, filename=filename, force_fail=force_fail)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Exposure error: %s", exc)
            async with state_lock:
                camera = STATE.camera
                camera.is_exposing = False
                camera.last_status = "failed"
                camera.last_image_path = None

    if background:
        asyncio.create_task(runner())
    else:
        await runner()
    return start_time


@app.get(f"{API_PREFIX}/status")
async def status() -> StatusResponse:
    """Return a snapshot of telescope, camera, sequence, and focuser state."""

    return _snapshot_state()


@app.post(f"{API_PREFIX}/telescope/connect")
async def telescope_connect(payload: ConnectionToggle) -> dict[str, Any]:
    async with state_lock:
        STATE.telescope.is_connected = payload.connect
        if not payload.connect:
            STATE.telescope.is_parked = True
    logger.info("Telescope connection set to %s", payload.connect)
    return {"connected": STATE.telescope.is_connected, "parked": STATE.telescope.is_parked}


@app.post(f"{API_PREFIX}/telescope/park")
async def telescope_park(payload: ParkRequest) -> dict[str, Any]:
    async with state_lock:
        if not STATE.telescope.is_connected:
            raise HTTPException(status_code=409, detail="telescope_disconnected")
        STATE.telescope.is_parked = payload.park
        if payload.park:
            STATE.telescope.is_slewing = False
    logger.info("Telescope park set to %s", payload.park)
    return {"parked": STATE.telescope.is_parked}


@app.post(f"{API_PREFIX}/telescope/slew")
async def telescope_slew(payload: SlewRequest) -> dict[str, Any]:
    """Simulate a telescope slew command."""

    async with state_lock:
        if not STATE.telescope.is_connected:
            raise HTTPException(status_code=409, detail="telescope_disconnected")
        if STATE.telescope.is_parked:
            raise HTTPException(status_code=409, detail="telescope_parked")
        STATE.telescope.is_slewing = True
    await asyncio.sleep(0.2)
    async with state_lock:
        STATE.telescope.ra_deg = payload.ra_deg
        STATE.telescope.dec_deg = payload.dec_deg
        STATE.telescope.is_slewing = False
        # TODO: enforce minimum altitude based on MOCK_NINA_MIN_ALT_DEG.
    logger.info("Telescope slewed to RA=%s Dec=%s", payload.ra_deg, payload.dec_deg)
    return {"status": "ok", "ra_deg": payload.ra_deg, "dec_deg": payload.dec_deg}


@app.get(f"{API_PREFIX}/telescope/position")
async def telescope_position() -> TelescopeState:
    return STATE.telescope


def _resolve_duration(value: float | None) -> float:
    return value if value is not None else settings.exposure_seconds


@app.post(f"{API_PREFIX}/camera/start_exposure")
async def camera_start_exposure(
    payload: CameraExposureRequest,
    background_tasks: BackgroundTasks,
    force_fail: bool = Query(False, description="Force this exposure to fail"),
) -> dict[str, Any]:
    duration = _resolve_duration(payload.exposure_seconds)
    try:
        start_time = await _start_exposure(duration, payload.filter, payload.binning)
    except ExposureRunningError:
        raise HTTPException(status_code=409, detail="exposure_already_running") from None
    except SequenceRunningError:
        raise HTTPException(status_code=409, detail="sequence_running") from None
    except TelescopeNotReadyError:
        raise HTTPException(status_code=409, detail="telescope_not_ready") from None

    async def runner() -> None:
        await _complete_exposure(duration, payload.filter, payload.binning, filename=payload.filename, force_fail=force_fail)

    background_tasks.add_task(runner)
    finish_time = start_time + timedelta(seconds=duration)
    return {"status": "started", "expected_finish_utc": finish_time.isoformat().replace("+00:00", "Z")}


@app.get(f"{API_PREFIX}/camera/status")
async def camera_status() -> CameraStatusResponse:
    return CameraStatusResponse(**STATE.camera.model_dump())


@app.post(f"{API_PREFIX}/sequence/start")
async def sequence_start(payload: SequenceStartRequest) -> dict[str, Any]:
    duration = _resolve_duration(payload.exposure_seconds)
    async with state_lock:
        if STATE.sequence.is_running:
            raise HTTPException(status_code=409, detail="sequence_already_running")
        if not STATE.telescope.is_connected:
            raise HTTPException(status_code=409, detail="telescope_disconnected")
        if STATE.telescope.is_parked:
            raise HTTPException(status_code=409, detail="telescope_parked")
        STATE.sequence.is_running = True
        STATE.sequence.current_index = 0
        STATE.sequence.total = payload.count
        STATE.sequence.name = payload.name

    async def runner() -> None:
        try:
            for idx in range(1, payload.count + 1):
                async with state_lock:
                    STATE.sequence.current_index = idx
                await _perform_exposure(
                    duration,
                    payload.filter,
                    payload.binning,
                    from_sequence=True,
                    background=False,
                )
        finally:
            async with state_lock:
                STATE.sequence.is_running = False
                STATE.sequence.current_index = 0
                STATE.sequence.name = None
        logger.info("Sequence %s completed", payload.name)

    asyncio.create_task(runner())
    logger.info("Sequence %s started (%s exposures)", payload.name, payload.count)
    return {"status": "started", "name": payload.name, "total": payload.count}


@app.get(f"{API_PREFIX}/sequence/status")
async def sequence_status() -> SequenceStatusResponse:
    return SequenceStatusResponse(**STATE.sequence.model_dump())


@app.post(f"{API_PREFIX}/sequence/abort")
async def sequence_abort() -> dict[str, Any]:
    async with state_lock:
        if not STATE.sequence.is_running:
            return {"status": "idle"}
        STATE.sequence.is_running = False
        STATE.sequence.current_index = 0
        STATE.sequence.name = None
    logger.info("Sequence aborted")
    return {"status": "aborted"}


@app.post(f"{API_PREFIX}/focuser/move")
async def focuser_move(payload: FocuserMoveRequest) -> dict[str, Any]:
    async with state_lock:
        STATE.focuser.is_moving = True
    await asyncio.sleep(0.1)
    async with state_lock:
        STATE.focuser.position = payload.position
        STATE.focuser.is_moving = False
    logger.info("Focuser moved to %s", payload.position)
    return {"position": STATE.focuser.position}


@app.get(f"{API_PREFIX}/focuser/status")
async def focuser_status() -> dict[str, Any]:
    return STATE.focuser.model_dump()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # type: ignore[override]
    logger.exception("Unhandled error for %s: %s", request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "internal_error"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mock_nina.main:app", host="0.0.0.0", port=settings.port, reload=False)
