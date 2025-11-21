"""Sequence template selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


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


TEMPLATES: tuple[SequenceTemplate, ...] = (
    SequenceTemplate(name="bright", max_vmag=15.0, exposure_seconds=20.0, count=8, filter="L", binning=1),
    SequenceTemplate(name="medium", max_vmag=18.5, exposure_seconds=45.0, count=10, filter="L", binning=1),
    SequenceTemplate(name="faint", max_vmag=99.0, exposure_seconds=90.0, count=12, filter="L", binning=2),
)


def select_template(vmag: float | None, urgency: float | None = None) -> SequenceTemplate:
    if vmag is None:
        return TEMPLATES[1]
    for template in TEMPLATES:
        if vmag <= template.max_vmag:
            return _apply_urgency(template, urgency)
    return _apply_urgency(TEMPLATES[-1], urgency)


def _apply_urgency(template: SequenceTemplate, urgency: float | None) -> SequenceTemplate:
    """Adjust exposure/count slightly when urgency is high."""
    if urgency is None or urgency < 0.7:
        return template
    scale = 0.85
    bump = 2 if template.count < 20 else 0
    return SequenceTemplate(
        name=f"{template.name}-urgent",
        max_vmag=template.max_vmag,
        exposure_seconds=template.exposure_seconds * scale,
        count=template.count + bump,
        filter=template.filter,
        binning=template.binning,
        tracking_mode=template.tracking_mode,
        focus_offset=template.focus_offset,
    )


def list_templates() -> Iterable[SequenceTemplate]:
    return TEMPLATES
