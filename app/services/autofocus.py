"""Autofocus -- Phase 4 of the direct_hardware branch.

Standard V-curve technique (the same approach NINA/SGP/INDI's own focus
modules use internally -- there's no separate standalone tool for this
the way PHD2 is for guiding, so this is a from-scratch build rather than
a protocol client): step the focuser through a range of positions,
measure a focus-quality metric (median star HFR) at each, fit a curve,
move to the position that minimizes it.

HFR (half-flux radius) here is computed as the flux-weighted mean radius
of background-subtracted pixels around each detected star's centroid --
sum(f_i * r_i) / sum(f_i) -- the same practical approximation widely used
by amateur focusing tools (PHD2's star-mass/HFD calculations and several
INDI drivers use this exact formula). It's not the mathematically precise
"radius containing exactly half the flux" definition, but correlates the
same way with focus quality and needs no PSF model fit, which makes it
more robust to the non-Gaussian, non-circular blur shapes a real optical
system produces well outside of focus.

Star detection reuses the same DAOStarFinder + sigma-clipped background
convention as AnalysisService.detect_sources() (app/services/analysis.py)
for consistency, rather than inventing a different detection approach.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from astropy.stats import sigma_clipped_stats
from photutils.detection import DAOStarFinder

logger = logging.getLogger(__name__)


@dataclass
class FocusSample:
    position: int
    median_hfr: float | None
    star_count: int


def measure_hfr(data: np.ndarray, fwhm_estimate: float = 4.0, box_radius: int = 12) -> tuple[float | None, int]:
    """Detect stars and compute the frame's median HFR (in pixels).

    Returns (median_hfr, star_count) -- median_hfr is None if no stars
    were detected (e.g. badly out of focus enough that DAOStarFinder's
    fixed fwhm_estimate no longer matches reality, or genuinely nothing
    in frame).
    """
    data = np.asarray(data, dtype=float)
    mean, median, std = sigma_clipped_stats(data, sigma=3.0)
    if std == 0:
        return None, 0
    threshold = 5.0 * std

    try:
        finder = DAOStarFinder(fwhm=fwhm_estimate, threshold=threshold)
        sources = finder(data - median)
    except Exception as exc:
        logger.debug("DAOStarFinder failed: %s", exc)
        return None, 0

    if sources is None or len(sources) == 0:
        return None, 0

    height, width = data.shape
    hfrs = []
    for source in sources:
        x0 = float(source["xcentroid"])
        y0 = float(source["ycentroid"])

        xi, yi = int(round(x0)), int(round(y0))
        x_lo, x_hi = max(0, xi - box_radius), min(width, xi + box_radius + 1)
        y_lo, y_hi = max(0, yi - box_radius), min(height, yi + box_radius + 1)
        if x_hi - x_lo < 3 or y_hi - y_lo < 3:
            continue

        cutout = data[y_lo:y_hi, x_lo:x_hi] - median
        yy, xx = np.mgrid[y_lo:y_hi, x_lo:x_hi]
        rr = np.sqrt((xx - x0) ** 2 + (yy - y0) ** 2)

        flux = np.clip(cutout, 0, None)  # background-subtracted, floor at 0
        total_flux = flux.sum()
        if total_flux <= 0:
            continue
        hfr = float((flux * rr).sum() / total_flux)
        hfrs.append(hfr)

    if not hfrs:
        return None, 0
    return float(np.median(hfrs)), len(hfrs)


def fit_v_curve_minimum(samples: list[FocusSample]) -> int | None:
    """Fit a parabola to (position, median_hfr) samples and return the
    position at its minimum, rounded to the nearest integer step.

    A parabola is a reasonable local approximation near the true V-curve
    minimum as long as the scanned range actually brackets it -- this
    isn't attempting to model the full, more linear V-curve shape far
    from focus. Returns None if there aren't enough valid samples or the
    fit doesn't have a real minimum (upward-opening parabola requires a
    positive leading coefficient; a fit that comes out concave-down or
    degenerate means the data doesn't actually show a V shape).
    """
    valid = [s for s in samples if s.median_hfr is not None]
    if len(valid) < 3:
        return None

    positions = np.array([s.position for s in valid], dtype=float)
    hfrs = np.array([s.median_hfr for s in valid], dtype=float)

    try:
        coeffs = np.polyfit(positions, hfrs, deg=2)
    except Exception as exc:
        logger.warning("V-curve parabola fit failed: %s", exc)
        return None

    a, b, _c = coeffs
    if a <= 0:
        logger.warning("V-curve fit is not concave-up (a=%.6g); data may not bracket a minimum", a)
        return None

    vertex = -b / (2 * a)
    if vertex < positions.min() or vertex > positions.max():
        logger.warning(
            "V-curve minimum (%.1f) falls outside the scanned range [%.0f, %.0f]; "
            "scan range likely doesn't bracket true focus",
            vertex,
            positions.min(),
            positions.max(),
        )
        return None

    return int(round(vertex))


__all__ = ["FocusSample", "measure_hfr", "fit_v_curve_minimum"]
