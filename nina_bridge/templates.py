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


TEMPLATES: tuple[SequenceTemplate, ...] = (
    SequenceTemplate(name="bright", max_vmag=15.0, exposure_seconds=20.0, count=8, filter="L", binning=1),
    SequenceTemplate(name="medium", max_vmag=18.5, exposure_seconds=45.0, count=10, filter="L", binning=1),
    SequenceTemplate(name="faint", max_vmag=99.0, exposure_seconds=90.0, count=12, filter="L", binning=2),
)


def select_template(vmag: float | None) -> SequenceTemplate:
    if vmag is None:
        return TEMPLATES[1]
    for template in TEMPLATES:
        if vmag <= template.max_vmag:
            return template
    return TEMPLATES[-1]


def list_templates() -> Iterable[SequenceTemplate]:
    return TEMPLATES

