"""Sequence template selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.presets import ExposurePreset, list_presets, select_preset


@dataclass
class SequenceTemplate:
    name: str
    max_vmag: float
    exposure_seconds: float
    count: int
    filter: str = "L"
    binning: int = 1
    tracking_mode: str = "sidereal"
    focus_offset: float | None = None


def _from_preset(preset: ExposurePreset) -> SequenceTemplate:
    return SequenceTemplate(
        name=preset.name,
        max_vmag=preset.max_vmag,
        exposure_seconds=preset.exposure_seconds,
        count=preset.count,
        filter=preset.filter,
        binning=preset.binning,
        tracking_mode=preset.tracking_mode,
        focus_offset=preset.focus_offset,
    )


def select_template(vmag: float | None, urgency: float | None = None) -> SequenceTemplate:
    return _from_preset(select_preset(vmag, profile=None, urgency=urgency))


def list_templates() -> Iterable[SequenceTemplate]:
    return (_from_preset(p) for p in list_presets())
