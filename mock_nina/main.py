"""Mock NINA FastAPI service."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import settings
from .fits_utils import create_dummy_fits
from .models import DeviceInfo, DeviceList, NinaResponse
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


def _success(response_data: Any) -> NinaResponse[Any]:
    return NinaResponse(Response=response_data)


def _error(message: str, status_code: int = 500) -> NinaResponse[Any]:
    return NinaResponse(
        Response=message, Error=message, StatusCode=status_code, Success=False
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
        "Exposure started: duration=%ss binning=%s (sequence=%s)",
        duration,
        binning,
        from_sequence,
    )
    return now


async def _complete_exposure(
    duration: float,
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
        # Mock filter is always "L" for now as we don't track filter state in exposure call
        create_dummy_fits(path, duration, "L", binning)
        status = "complete"
    async with state_lock:
        camera = STATE.camera
        camera.is_exposing = False
        camera.last_status = status
        camera.last_image_path = path
    logger.info("Exposure %s", status)


@app.get(f"{API_PREFIX}/status")
async def status() -> NinaResponse[dict[str, Any]]:
    """Return a snapshot of telescope, camera, sequence, and focuser state."""
    async with state_lock:
        data = {
            "telescope": STATE.telescope.model_dump(),
            "camera": STATE.camera.model_dump(),
            "sequence": STATE.sequence.model_dump(),
            "focuser": STATE.focuser.model_dump(),
        }
    return _success(data)


# --- Equipment / Mount ---

@app.get(f"{API_PREFIX}/equipment/mount/connect")
async def mount_connect(to: Optional[str] = None) -> NinaResponse[str]:
    async with state_lock:
        STATE.telescope.is_connected = True
        STATE.telescope.is_parked = False  # Unpark on connect? NINA behavior varies, but let's assume unparked.
    logger.info("Mount connected")
    return _success("Connected")


@app.get(f"{API_PREFIX}/equipment/mount/disconnect")
async def mount_disconnect() -> NinaResponse[str]:
    async with state_lock:
        STATE.telescope.is_connected = False
        STATE.telescope.is_parked = True
    logger.info("Mount disconnected")
    return _success("Disconnected")


@app.get(f"{API_PREFIX}/equipment/mount/park")
async def mount_park() -> NinaResponse[str]:
    async with state_lock:
        if not STATE.telescope.is_connected:
             return _error("Mount not connected", 409)
        STATE.telescope.is_parked = True
        STATE.telescope.is_slewing = False
    logger.info("Mount parked")
    return _success("Parking")


@app.get(f"{API_PREFIX}/equipment/mount/unpark")
async def mount_unpark() -> NinaResponse[str]:
    async with state_lock:
        if not STATE.telescope.is_connected:
             return _error("Mount not connected", 409)
        STATE.telescope.is_parked = False
    logger.info("Mount unparked")
    return _success("Unparking")


@app.get(f"{API_PREFIX}/equipment/mount/slew")
async def mount_slew(
    ra: float,
    dec: float,
    waitForResult: bool = False,
) -> NinaResponse[str]:
    async with state_lock:
        if not STATE.telescope.is_connected:
            return _error("Mount not connected", 409)
        if STATE.telescope.is_parked:
            return _error("Mount parked", 409)
        STATE.telescope.is_slewing = True

    # Simulate slew time
    await asyncio.sleep(0.2)

    async with state_lock:
        STATE.telescope.ra_deg = ra
        STATE.telescope.dec_deg = dec
        STATE.telescope.is_slewing = False

    logger.info("Mount slewed to RA=%s Dec=%s", ra, dec)
    return _success("Slew finished")


@app.get(f"{API_PREFIX}/equipment/mount/tracking")
async def mount_set_tracking(mode: int) -> NinaResponse[str]:
    # 0: Sidereal, 1: Lunar, 2: Solar, 3: King, 4: Stopped
    modes = {0: "Sidereal", 1: "Lunar", 2: "Solar", 3: "King", 4: "Stopped"}
    if mode not in modes:
        return _error("Invalid tracking mode", 409)
    
    async with state_lock:
        STATE.telescope.tracking_mode = modes[mode]
    
    logger.info("Tracking set to %s", modes[mode])
    return _success("Tracking mode changed")


# --- Equipment / Camera ---

@app.get(f"{API_PREFIX}/equipment/camera/connect")
async def camera_connect(to: Optional[str] = None) -> NinaResponse[str]:
    # No explicit camera state in mock yet, assume always connected or add state later
    return _success("Connected")


@app.get(f"{API_PREFIX}/equipment/camera/capture")
async def camera_capture(
    duration: float = 1.0,
    binning: int = 1, # Note: NINA API doesn't pass binning here usually, it's set via set-binning, but for mock simplicity we accept it or ignore
    save: bool = True,
) -> NinaResponse[str]:
    try:
        # NINA API capture endpoint returns "Capture started" immediately
        start_time = await _start_exposure(duration, binning)
    except ExposureRunningError:
        return _error("Camera currently exposing", 409)
    except TelescopeNotReadyError:
        return _error("Camera not connected", 409)
    except SequenceRunningError:
        return _error("Sequence running", 409)

    async def runner() -> None:
        await _complete_exposure(duration, binning)

    asyncio.create_task(runner())
    return _success("Capture started")


@app.get(f"{API_PREFIX}/equipment/camera/abort-exposure")
async def camera_abort() -> NinaResponse[str]:
    async with state_lock:
        if not STATE.camera.is_exposing:
             return _error("Camera not exposing", 409)
        STATE.camera.is_exposing = False
        STATE.camera.last_status = "aborted"
    return _success("Exposure aborted")


# --- Equipment / FilterWheel ---

@app.get(f"{API_PREFIX}/equipment/filterwheel/change-filter")
async def filter_change(filterId: int) -> NinaResponse[str]:
    # Mock filter change
    logger.info("Filter changed to ID %s", filterId)
    return _success("Filter changed")


# --- Equipment / Focuser ---

@app.get(f"{API_PREFIX}/equipment/focuser/move")
async def focuser_move(position: int) -> NinaResponse[str]:
    async with state_lock:
        STATE.focuser.is_moving = True
    await asyncio.sleep(0.1)
    async with state_lock:
        STATE.focuser.position = position
        STATE.focuser.is_moving = False
    logger.info("Focuser moved to %s", position)
    return _success("Move started")


@app.get(f"{API_PREFIX}/equipment/focuser/info")
async def focuser_info() -> NinaResponse[dict]:
    return _success({
        "Position": STATE.focuser.position,
        "IsMoving": STATE.focuser.is_moving,
        "Temperature": 20.0,
        "StepSize": 1.0,
    })


# --- Equipment / Dome ---

@app.get(f"{API_PREFIX}/equipment/dome/connect")
async def dome_connect() -> NinaResponse[str]:
    return _success("Connected")

@app.get(f"{API_PREFIX}/equipment/dome/open")
async def dome_open() -> NinaResponse[str]:
    return _success("Shutter opening")

@app.get(f"{API_PREFIX}/equipment/dome/close")
async def dome_close() -> NinaResponse[str]:
    return _success("Shutter closing")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):  # type: ignore[override]
    logger.exception("Unhandled error for %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content=NinaResponse(
            Response="Internal Error",
            Error=str(exc),
            StatusCode=500,
            Success=False
        ).model_dump()
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mock_nina.main:app", host="0.0.0.0", port=settings.port, reload=False)
