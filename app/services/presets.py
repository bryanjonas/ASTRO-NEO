"""Exposure preset selection based on magnitude and equipment profile."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.equipment import EquipmentProfile


@dataclass(frozen=True)
class ExposurePreset:
    name: str
    max_vmag: float
    exposure_seconds: float
    count: int
    filter: str
    binning: int
    tracking_mode: str = "sidereal"
    focus_offset: float | None = None


DEFAULT_PRESETS: tuple[ExposurePreset, ...] = (
    ExposurePreset(name="bright", max_vmag=15.0, exposure_seconds=20.0, count=8, filter="L", binning=1),
    ExposurePreset(name="medium", max_vmag=18.5, exposure_seconds=45.0, count=10, filter="L", binning=1),
    ExposurePreset(name="faint", max_vmag=99.0, exposure_seconds=90.0, count=12, filter="L", binning=2),
)


def list_presets(profile: EquipmentProfile | None = None) -> Iterable[ExposurePreset]:
    """Return presets; profile hook reserved for future per-profile overrides."""
    return DEFAULT_PRESETS


def select_preset(vmag: float | None, profile: EquipmentProfile | None = None, urgency: float | None = None) -> ExposurePreset:
    """Choose an exposure preset based on target magnitude and optional urgency."""
    presets = list_presets(profile)
    if vmag is None:
        return presets[1]
    for preset in presets:
        if vmag <= preset.max_vmag:
            return _apply_urgency(preset, urgency)
    return _apply_urgency(presets[-1], urgency)


def _apply_urgency(preset: ExposurePreset, urgency: float | None) -> ExposurePreset:
    """Adjust exposure/count slightly when urgency is high."""
    if urgency is None or urgency < 0.7:
        return preset
    scale = 0.85
    bump = 2 if preset.count < 20 else 0
    return ExposurePreset(
        name=f"{preset.name}-urgent",
        max_vmag=preset.max_vmag,
        exposure_seconds=preset.exposure_seconds * scale,
        count=preset.count + bump,
        filter=preset.filter,
        binning=preset.binning,
        tracking_mode=preset.tracking_mode,
        focus_offset=preset.focus_offset,
    )


__all__ = ["ExposurePreset", "select_preset", "list_presets", "DEFAULT_PRESETS"]
