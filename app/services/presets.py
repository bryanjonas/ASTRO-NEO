"""Exposure preset selection based on magnitude and equipment profile."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

from app.core.config import settings
from app.services.equipment import EquipmentProfile

logger = logging.getLogger(__name__)


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
    ExposurePreset(
        name="bright",
        max_vmag=16.0,
        exposure_seconds=60.0,
        count=6,
        filter="L",
        binning=1,
        delay_seconds=90.0,
        gain=250,
        offset=20,
    ),
    ExposurePreset(
        name="medium",
        max_vmag=18.0,
        exposure_seconds=90.0,
        count=8,
        filter="L",
        binning=1,
        delay_seconds=120.0,
        gain=250,
        offset=20,
    ),
    ExposurePreset(
        name="faint",
        max_vmag=99.0,
        exposure_seconds=120.0,
        count=10,
        filter="L",
        binning=2,
        delay_seconds=180.0,
        gain=250,
        offset=20,
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
    vmag: float | None,
    profile: EquipmentProfile | None = None,
    urgency: float | None = None,
    default_name: str = "bright",
    motion_rate_arcsec_min: float | None = None,
    pixel_scale_arcsec_per_pixel: float | None = None,
) -> ExposurePreset:
    """Choose an exposure preset based on target magnitude, urgency, and motion rate.

    Args:
        vmag: Target V magnitude
        profile: Equipment profile for overrides
        urgency: Urgency factor (0-1)
        default_name: Default preset name if vmag is None
        motion_rate_arcsec_min: Target motion rate (arcsec/min) for fast-mover adaptation
        pixel_scale_arcsec_per_pixel: Pixel scale for trailing calculations

    Returns:
        ExposurePreset adapted for target characteristics
    """
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
    pixel_scale = pixel_scale_arcsec_per_pixel or settings.astrometry_pixel_scale_arcsec
    chosen = _apply_astrometric_rules(
        chosen,
        vmag=vmag,
        motion_rate_arcsec_min=motion_rate_arcsec_min,
        pixel_scale_arcsec_per_pixel=pixel_scale,
    )
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
        gain=preset.gain,
        offset=preset.offset,
    )


def _apply_astrometric_rules(
    preset: ExposurePreset,
    *,
    vmag: float | None,
    motion_rate_arcsec_min: float | None,
    pixel_scale_arcsec_per_pixel: float,
) -> ExposurePreset:
    """Apply motion/brightness rules to construct an astrometric imaging plan."""

    exposure = preset.exposure_seconds
    count = preset.count
    delay = preset.delay_seconds
    binning = preset.binning

    if vmag is not None and vmag >= 19.0 and binning == 1:
        binning = 2

    if motion_rate_arcsec_min and motion_rate_arcsec_min > 0:
        motion_arcsec_per_sec = motion_rate_arcsec_min / 60.0
        if motion_arcsec_per_sec > 0:
            seeing_limit = settings.astrometry_default_seeing_arcsec / motion_arcsec_per_sec
        else:
            seeing_limit = settings.astrometry_max_exposure_seconds
        trailing_arcsec = settings.astrometry_max_trailing_pixels * pixel_scale_arcsec_per_pixel
        trailing_limit = (trailing_arcsec * 60.0) / motion_rate_arcsec_min
        exposure = min(exposure, seeing_limit, trailing_limit)

        if motion_rate_arcsec_min >= 60:
            delay = min(delay, 60.0)
        elif motion_rate_arcsec_min >= 40:
            delay = min(delay, 90.0)
        elif motion_rate_arcsec_min >= 20:
            delay = min(delay, 120.0)

    exposure = max(
        settings.astrometry_min_exposure_seconds,
        min(exposure, settings.astrometry_max_exposure_seconds),
    )

    total_integration = preset.count * preset.exposure_seconds
    if total_integration <= 0:
        total_integration = exposure * max(1, preset.count)
    count = max(
        settings.astrometry_min_frames,
        math.ceil(total_integration / exposure),
    )
    count = min(count, settings.astrometry_max_frames)

    delay = max(settings.astrometry_min_delay_seconds, min(delay, settings.astrometry_max_delay_seconds))

    if (
        motion_rate_arcsec_min
        and motion_rate_arcsec_min >= 50
        and count < settings.astrometry_max_frames
    ):
        count = min(settings.astrometry_max_frames, count + 1)

    return ExposurePreset(
        name=f"{preset.name}-auto",
        max_vmag=preset.max_vmag,
        exposure_seconds=exposure,
        count=count,
        filter=preset.filter,
        binning=binning,
        delay_seconds=delay,
        tracking_mode=preset.tracking_mode,
        focus_offset=preset.focus_offset,
        gain=preset.gain,
        offset=preset.offset,
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
