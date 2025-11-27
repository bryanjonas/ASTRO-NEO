"""Pydantic models for mock NINA API schemas."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class NinaResponse(BaseModel, Generic[T]):
    """Standard NINA API response envelope."""

    Response: T
    Error: str = ""
    StatusCode: int = 200
    Success: bool = True
    Type: str = "API"


class DeviceInfo(BaseModel):
    Id: str
    Name: str
    Connected: bool


class DeviceList(BaseModel):
    Devices: list[DeviceInfo]


__all__ = [
    "NinaResponse",
    "DeviceInfo",
    "DeviceList",
]
