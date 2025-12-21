"""Thin wrapper around astrometry.net solve-field."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits

from app.core.config import settings


class SolveError(RuntimeError):
    pass


def solve_fits(
    fits_path: str | Path,
    radius_deg: float | None = None,
    ra_hint: float | None = None,
    dec_hint: float | None = None,
    downsample: int | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """Run solve-field locally (synchronous subprocess)."""

    return _solve_local(
        fits_path,
        radius_deg=radius_deg,
        ra_hint=ra_hint,
        dec_hint=dec_hint,
        downsample=downsample,
        timeout=timeout or settings.astrometry_solve_timeout,
    )


def _solve_local(
    fits_path: str | Path,
    *,
    radius_deg: float | None,
    ra_hint: float | None,
    dec_hint: float | None,
    downsample: int | None,
    timeout: int,
) -> dict[str, Any]:
    path = Path(fits_path)
    if not path.exists():
        raise SolveError(f"FITS not found: {path}")

    def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                cwd=str(path.parent),
            )
        except subprocess.CalledProcessError as exc:
            raise SolveError(exc.stderr or exc.stdout or str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise SolveError("solve-field timed out") from exc

    base_cmd = [
        "solve-field",
        "--overwrite",
        "--no-plots",
        "--dir",
        str(path.parent),
        "--config",
        settings.astrometry_config_path,
    ]
    if settings.astrometry_search_radius_deg is not None:
        base_cmd += ["--radius", str(settings.astrometry_search_radius_deg)]
    if radius_deg is not None:
        base_cmd += ["--radius", str(radius_deg)]
    if ra_hint is not None and dec_hint is not None:
        base_cmd += ["--ra", str(ra_hint), "--dec", str(dec_hint)]
    eff_downsample = downsample or settings.astrometry_downsample
    if eff_downsample:
        base_cmd += ["--downsample", str(eff_downsample)]
    low = settings.astrometry_scale_low_arcsec
    high = settings.astrometry_scale_high_arcsec
    if low and high:
        base_cmd += ["--scale-units", "arcsecperpix", "--scale-low", str(low), "--scale-high", str(high)]

    # First try JSON output (newer astrometry.net)
    try:
        result = _run(base_cmd + ["--json", str(path)])
        output = result.stdout.strip()
        # solve-field might output text before/after JSON
        # Try to find the JSON object
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            # Try to find { ... }
            import re
            match = re.search(r"(\{.*\})", output, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise
    except SolveError as exc:
        if "--json" not in str(exc):
            raise
        # Fallback to legacy mode (no --json); parse .wcs instead
        res = _run(base_cmd + [str(path)])
        
        # Log full output for debugging
        import logging
        logging.info("solve-field stdout: %s", res.stdout)
        logging.info("solve-field stderr: %s", res.stderr)
        
        solution = _parse_wcs_solution(path)

        # Copy WCS headers from .wcs file back to original FITS
        _copy_wcs_to_fits(path)

        # Try to extract RMS from stdout
        import re
        match = re.search(r"RMS:\s+([0-9]*\.?[0-9]+)\s+arcsec", res.stdout)
        if match:
            solution["solution"]["rms"] = float(match.group(1))
        else:
            # Try to calculate from .corr file
            rms = _calculate_rms_from_corr(path)
            if rms is not None:
                solution["solution"]["rms"] = rms
            else:
                logging.warning("Could not extract RMS from solve-field output or .corr file")

        return solution


def _calculate_rms_from_corr(fits_path: Path) -> float | None:
    """Calculate RMS error from the .corr file produced by solve-field."""
    corr_path = fits_path.with_suffix(".corr")
    if not corr_path.exists():
        # Sometimes it might be named differently?
        return None
        
    try:
        with fits.open(corr_path) as hdul:
            if len(hdul) < 2:
                return None
            data = hdul[1].data
            if data is None or len(data) == 0:
                return None
                
            # Check for RA/Dec columns
            if "field_ra" in data.names and "index_ra" in data.names:
                field_ra = data["field_ra"]
                field_dec = data["field_dec"]
                index_ra = data["index_ra"]
                index_dec = data["index_dec"]
                
                # Calculate angular separation
                # Simple approximation for small offsets
                d_dec = field_dec - index_dec
                d_ra = (field_ra - index_ra) * np.cos(np.radians(index_dec))
                
                dist_sq = d_ra**2 + d_dec**2
                rms_deg = np.sqrt(np.mean(dist_sq))
                return float(rms_deg * 3600.0)
                
    except Exception as exc:
        import logging
        logging.warning("Failed to calculate RMS from .corr: %s", exc)
        return None


def _copy_wcs_to_fits(fits_path: Path) -> None:
    """Copy WCS headers from .wcs file into the original FITS file."""
    import logging
    wcs_path = fits_path.with_suffix(".wcs")
    if not wcs_path.exists():
        logging.warning(f"No .wcs file to copy from: {wcs_path}")
        return

    try:
        # Read WCS headers
        wcs_hdr = fits.getheader(wcs_path)

        # Open original FITS and update header
        with fits.open(fits_path, mode='update') as hdul:
            # WCS keywords to copy
            wcs_keywords = [
                'WCSAXES', 'CTYPE1', 'CTYPE2', 'EQUINOX', 'LONPOLE', 'LATPOLE',
                'CRVAL1', 'CRVAL2', 'CRPIX1', 'CRPIX2', 'CUNIT1', 'CUNIT2',
                'CD1_1', 'CD1_2', 'CD2_1', 'CD2_2',
                'CDELT1', 'CDELT2', 'CROTA1', 'CROTA2',
                'IMAGEW', 'IMAGEH', 'A_ORDER', 'B_ORDER', 'A_0_0', 'A_0_1',
                'A_0_2', 'A_1_0', 'A_1_1', 'A_2_0', 'B_0_0', 'B_0_1',
                'B_0_2', 'B_1_0', 'B_1_1', 'B_2_0', 'AP_ORDER', 'BP_ORDER',
                'AP_0_0', 'AP_0_1', 'AP_0_2', 'AP_1_0', 'AP_1_1', 'AP_2_0',
                'BP_0_0', 'BP_0_1', 'BP_0_2', 'BP_1_0', 'BP_1_1', 'BP_2_0',
            ]

            # Copy WCS keywords
            for keyword in wcs_keywords:
                if keyword in wcs_hdr:
                    hdul[0].header[keyword] = wcs_hdr[keyword]

            # Also copy COMMENT cards related to astrometry.net
            for card in wcs_hdr.cards:
                if card.keyword == 'COMMENT' and 'astrometry.net' in str(card.value).lower():
                    hdul[0].header.add_comment(card.value)

        logging.info(f"Copied WCS headers to {fits_path.name}")
    except Exception as exc:
        logging.error(f"Failed to copy WCS headers to {fits_path}: {exc}")


def _parse_wcs_solution(fits_path: Path) -> dict[str, Any]:
    """Parse the .wcs header produced by solve-field when --json is unavailable."""
    wcs_path = fits_path.with_suffix(".wcs")
    if not wcs_path.exists():
        raise SolveError(f"Solve completed but {wcs_path} not found")
    hdr = fits.getheader(wcs_path)
    ra = hdr.get("CRVAL1")
    dec = hdr.get("CRVAL2")
    cd11 = hdr.get("CD1_1") or hdr.get("CDELT1")
    cd22 = hdr.get("CD2_2") or hdr.get("CDELT2")
    cd12 = hdr.get("CD1_2") or 0.0
    cd21 = hdr.get("CD2_1") or 0.0
    # Pixel scale (arcsec/pixel) from CD matrix
    scales = []
    if cd11 and cd22:
        scales.append(abs(cd11) * 3600.0)
        scales.append(abs(cd22) * 3600.0)
    scale_arcsec = float(sum(scales) / len(scales)) if scales else None
    orientation_deg = None
    if cd11 is not None and cd12 is not None:
        orientation_deg = math.degrees(math.atan2(cd12, cd11))
    return {
        "solution": {
            "ra": ra,
            "dec": dec,
            "pixscale": scale_arcsec,
            "orientation": orientation_deg,
        }
    }


__all__ = ["solve_fits", "SolveError"]
