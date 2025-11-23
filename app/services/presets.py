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
    delay_seconds: float = 60.0  # Delay between exposures for motion detection
    tracking_mode: str = "sidereal"
    focus_offset: float | None = None
    gain: int | None = None
    offset: int | None = None


DEFAULT_PRESETS: tuple[ExposurePreset, ...] = (
    # NEOCP presets optimized for astrometry: 3-6 exposures, 60-120s each, 1-3 min spacing
    ExposurePreset(
        name="bright",
        max_vmag=16.0,
        exposure_seconds=60.0,
        count=4,
        filter="L",
        binning=1,
        delay_seconds=90.0,  # 1.5 min spacing for motion detection
    ),
    ExposurePreset(
        name="medium",
        max_vmag=18.0,
        exposure_seconds=90.0,
        count=5,
        filter="L",
        binning=1,
        delay_seconds=120.0,  # 2 min spacing
    ),
    ExposurePreset(
        name="faint",
        max_vmag=99.0,
        exposure_seconds=120.0,
        count=6,
        filter="L",
        binning=2,
        delay_seconds=180.0,  # 3 min spacing for slower objects
    ),
)


def _coerce_preset(data: ExposurePreset | dict) -> ExposurePreset:
    if isinstance(data, ExposurePreset):
        return data
    return ExposurePreset(**data)


def list_presets(profile: EquipmentProfile | None = None) -> Iterable[ExposurePreset]:
    """Return presets, honoring any overrides on the active equipment profile."""
    if profile and getattr(profile, "presets", None):
        try:
            return [_coerce_preset(item) for item in profile.presets]
        except Exception:
            # If profile presets are invalid, fall back to defaults
            return DEFAULT_PRESETS
    return DEFAULT_PRESETS


def select_preset(
    vmag: float | None, profile: EquipmentProfile | None = None, urgency: float | None = None, default_name: str = "bright"
) -> ExposurePreset:
    """Choose an exposure preset based on target magnitude and optional urgency."""
    presets = list_presets(profile)
    chosen = None
    if vmag is None:
        for preset in presets:
            if preset.name == default_name:
                chosen = preset
                break
    if chosen is None:
        for preset in presets:
            if vmag <= preset.max_vmag:
                chosen = preset
                break
        if chosen is None:
            chosen = presets[-1]
    chosen = _apply_urgency(chosen, urgency)
    return _apply_profile_overrides(chosen, profile)


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
        delay_seconds=preset.delay_seconds * 0.8,  # Tighter spacing for urgent targets
        tracking_mode=preset.tracking_mode,
        focus_offset=preset.focus_offset,
    )


def _apply_profile_overrides(preset: ExposurePreset, profile: EquipmentProfile | None) -> ExposurePreset:
    """Apply per-profile defaults for filter/gain/offset."""
    if not profile:
        return preset
    camera = profile.camera
    filter_name = preset.filter
    if camera.filters:
        filter_name = camera.filters[0]
    gain = preset.gain
    offset = preset.offset
    if camera.gain_presets:
        gain = camera.gain_presets.get(preset.name, gain)
    if camera.offset_presets:
        offset = camera.offset_presets.get(preset.name, offset)
    return ExposurePreset(
        name=preset.name,
        max_vmag=preset.max_vmag,
        exposure_seconds=preset.exposure_seconds,
        count=preset.count,
        filter=filter_name,
        binning=preset.binning,
        delay_seconds=preset.delay_seconds,
        tracking_mode=preset.tracking_mode,
        focus_offset=preset.focus_offset,
        gain=gain,
        offset=offset,
    )


__all__ = ["ExposurePreset", "select_preset", "list_presets", "DEFAULT_PRESETS"]
