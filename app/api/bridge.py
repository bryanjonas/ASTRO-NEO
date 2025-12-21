"""API endpoints that proxy to the NINA bridge service."""

from __future__ import annotations

from typing import Any

from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel, Field

from app.services.automation import AutomationService, TargetPlan
from app.services.nina_client import NinaBridgeService

router = APIRouter(prefix="/bridge", tags=["bridge"])


def get_bridge() -> NinaBridgeService:
    return NinaBridgeService()


class OverridePayload(BaseModel):
    manual_override: bool


class DomePayload(BaseModel):
    closed: bool


class ConnectPayload(BaseModel):
    connect: bool


class ParkPayload(BaseModel):
    park: bool


class SlewPayload(BaseModel):
    ra_deg: float = Field(..., ge=0.0, lt=360.0)
    dec_deg: float = Field(..., ge=-90.0, le=90.0)


class FocuserMovePayload(BaseModel):
    position: int = Field(..., ge=0)
    speed: int | None = Field(default=None, ge=1)


class ExposurePayload(BaseModel):
    filter: str = Field(..., min_length=1, max_length=16)
    binning: int = Field(..., ge=1, le=4)
    exposure_seconds: float | None = Field(default=None, gt=0)
    target: str | None = Field(default=None, max_length=128)


class SequencePlanPayload(BaseModel):
    vmag: float | None = Field(default=None, ge=0.0)
    urgency: float | None = Field(default=None, ge=0.0, le=1.0)
    profile: str | None = Field(default=None, max_length=64)


class SequenceStartPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    count: int = Field(..., ge=1, le=999)
    filter: str = Field(..., min_length=1, max_length=16)
    binning: int = Field(..., ge=1, le=4)
    exposure_seconds: float | None = Field(default=None, gt=0)
    target: str | None = Field(default=None, max_length=128)
    tracking_mode: str | None = Field(default=None, max_length=32)


class AutomationPayload(BaseModel):
    target: str = Field(..., min_length=1, max_length=128)
    ra_deg: float = Field(..., ge=0.0, lt=360.0)
    dec_deg: float = Field(..., ge=-90.0, le=90.0)
    filter: str | None = Field(default=None, min_length=1, max_length=16)
    binning: int | None = Field(default=None, ge=1, le=4)
    exposure_seconds: float | None = Field(default=None, gt=0)
    count: int | None = Field(default=None, ge=1, le=999)
    vmag: float | None = Field(default=None, ge=0.0)
    urgency: float | None = Field(default=None, ge=0.0, le=1.0)
    focus_position: int | None = Field(default=None, ge=0)
    park_after: bool = Field(default=False)


@router.get("/status")
def bridge_status(bridge: NinaBridgeService = Depends(get_bridge)) -> Any:
    return bridge.get_status()


@router.get("/equipment")
def equipment_profile(bridge: NinaBridgeService = Depends(get_bridge)) -> Any:
    return bridge.equipment_profile()


@router.post("/override")
def set_override(
    payload: OverridePayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.set_override(payload.manual_override)


@router.post("/dome")
def set_dome(payload: DomePayload, bridge: NinaBridgeService = Depends(get_bridge)) -> Any:
    return bridge.set_dome(payload.closed)


class IgnoreWeatherPayload(BaseModel):
    ignore_weather: bool


@router.post("/ignore_weather")
def set_ignore_weather(
    payload: IgnoreWeatherPayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.set_ignore_weather(payload.ignore_weather)


@router.post("/telescope/connect")
def telescope_connect(
    payload: ConnectPayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.connect_telescope(payload.connect)


@router.post("/telescope/park")
def telescope_park(
    payload: ParkPayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.park_telescope(payload.park)


@router.post("/telescope/slew")
def telescope_slew(
    payload: SlewPayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.slew(payload.ra_deg, payload.dec_deg)


@router.get("/telescopes")
def list_telescopes(bridge: NinaBridgeService = Depends(get_bridge)) -> Any:
    return bridge.list_telescopes()


@router.post("/telescope/connect_device")
def connect_telescope_device(
    device_id: str = Form(...), bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.connect_telescope(connect=True, device_id=device_id)


@router.post("/focuser/move")
def focuser_move(
    payload: FocuserMovePayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.focuser_move(payload.position, payload.speed)


@router.post("/camera/exposure")
def start_exposure(
    payload: ExposurePayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    result = bridge.start_exposure(payload.filter, payload.binning, payload.exposure_seconds, target=payload.target)
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail={"reason": "invalid_capture_response"})
    return {"result": result}


@router.get("/cameras")
def list_cameras(bridge: NinaBridgeService = Depends(get_bridge)) -> Any:
    return bridge.list_cameras()


class CameraConnectPayload(BaseModel):
    device_id: str


@router.post("/camera/connect")
def connect_camera(
    device_id: str = Form(...), bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.connect_camera(device_id)


@router.post("/sequence/plan")
def plan_sequence(
    payload: SequencePlanPayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    return bridge.plan_sequence(payload.model_dump(exclude_none=True))


@router.post("/sequence/start")
def start_sequence(
    payload: SequenceStartPayload, bridge: NinaBridgeService = Depends(get_bridge)
) -> Any:
    result = bridge.start_sequence(payload.model_dump(exclude_none=True))
    return {"result": result}


@router.post("/automation/run")
def automation_run(payload: AutomationPayload) -> Any:
    automation = AutomationService()
    # build_target_plan expects a dictionary with: name, candidate_id, ra_deg, dec_deg, vmag (optional)
    target_dict = {
        "name": payload.target,
        "candidate_id": payload.target,  # Use target name as candidate_id
        "ra_deg": payload.ra_deg,
        "dec_deg": payload.dec_deg,
        "vmag": payload.vmag,
    }
    plan = automation.build_target_plan(target_dict)
    return automation.execute_target_plan(plan)
