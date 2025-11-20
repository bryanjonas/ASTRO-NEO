"""Simple runtime state for the bridge service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BridgeState:
    manual_override: bool = False
    dome_closed: bool = False


STATE = BridgeState()

__all__ = ["STATE", "BridgeState"]
