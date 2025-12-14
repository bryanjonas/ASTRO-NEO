"""Exposure preset selection based on magnitude and equipment profile."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable

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
    # NEOCP presets optimized for astrometry: 3-6 exposures, 60-120s each, 1-3 min spacing
    ExposurePreset(
        name="bright",
        max_vmag=16.0,
        exposure_seconds=60.0,
        count=4,
        filter="L",
        binning=1,
        delay_seconds=90.0,  # 1.5 min spacing for motion detection
        gain=250,
        offset=20,
    ),
    ExposurePreset(
        name="medium",
        max_vmag=18.0,
        exposure_seconds=90.0,
        count=5,
        filter="L",
        binning=1,
        delay_seconds=120.0,  # 2 min spacing
        gain=250,
        offset=20,
    ),
    ExposurePreset(
        name="faint",
        max_vmag=99.0,
        exposure_seconds=120.0,
        count=6,
        filter="L",
        binning=2,
        delay_seconds=180.0,  # 3 min spacing for slower objects
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
    pixel_scale_arcsec_per_pixel: float = 1.5,
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
    chosen = _apply_fast_mover_adaptation(chosen, motion_rate_arcsec_min, pixel_scale_arcsec_per_pixel)
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


def _apply_fast_mover_adaptation(
    preset: ExposurePreset,
    motion_rate_arcsec_min: float | None,
    pixel_scale_arcsec_per_pixel: float,
) -> ExposurePreset:
    """Adapt preset for fast-moving targets to limit trailing.

    Strategy:
    - For motion > 30 "/min: Reduce exposure time to keep trailing < 5 pixels
    - Increase count to maintain total integration time (SNR)
    - Reduce delay between exposures for tighter temporal sampling

    Args:
        preset: Base exposure preset
        motion_rate_arcsec_min: Apparent motion rate (arcsec/min)
        pixel_scale_arcsec_per_pixel: Image scale for trailing calculation

    Returns:
        Adapted preset with shorter exposures and more frames
    """
    if not motion_rate_arcsec_min or motion_rate_arcsec_min < 30:
        return preset  # No adaptation needed for slow movers

    # Target: keep trailing < 5 pixels
    max_trailing_pixels = 5.0
    max_trailing_arcsec = max_trailing_pixels * pixel_scale_arcsec_per_pixel

    # Calculate maximum exposure time to limit trailing
    # trailing_arcsec = motion_rate_arcsec_min * exposure_seconds / 60
    # Solve for exposure_seconds: exposure_seconds = (max_trailing_arcsec * 60) / motion_rate_arcsec_min
    max_exposure_seconds = (max_trailing_arcsec * 60.0) / motion_rate_arcsec_min

    if preset.exposure_seconds <= max_exposure_seconds:
        return preset  # Already short enough

    # Reduce exposure time
    original_exposure = preset.exposure_seconds
    adapted_exposure = max_exposure_seconds

    # Increase count to maintain total integration time
    # total_integration = count * exposure
    # new_count = (original_count * original_exposure) / adapted_exposure
    scale_factor = original_exposure / adapted_exposure
    adapted_count = max(preset.count, int(math.ceil(preset.count * scale_factor)))

    # Reduce delay for tighter temporal sampling
    adapted_delay = max(30.0, preset.delay_seconds * 0.5)

    logger.info(
        "Fast mover detected (%.1f\"/min): reducing exposure from %.1fs to %.1fs, "
        "increasing count from %d to %d, reducing delay to %.1fs",
        motion_rate_arcsec_min,
        original_exposure,
        adapted_exposure,
        preset.count,
        adapted_count,
        adapted_delay,
    )

    return ExposurePreset(
        name=f"{preset.name}-fast",
        max_vmag=preset.max_vmag,
        exposure_seconds=adapted_exposure,
        count=adapted_count,
        filter=preset.filter,
        binning=preset.binning,
        delay_seconds=adapted_delay,
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
