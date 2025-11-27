"""Simple runtime state for the bridge service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BridgeState:
    manual_override: bool = False
    ignore_weather: bool = False
    dome_closed: bool = False
    sequence_running: bool = False
    last_sequence_name: str | None = None


STATE = BridgeState()

__all__ = ["STATE", "BridgeState"]
