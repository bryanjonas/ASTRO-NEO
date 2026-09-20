"""Write FITS files from raw Alpaca camera captures -- Phase 2 of the
direct_hardware branch.

Matches NINA's existing filename convention and header field set closely
enough that file_poller.py's glob pattern and everything downstream
(solver, analysis) needs zero changes -- confirmed by direct comparison
against a real NINA-captured FITS from the same session (see the commit
that added this). Deliberately scoped to camera/FITS concerns only: RA/Dec,
target, and other capture-context fields are supplied by the caller
(sequential_capture.py) rather than looked up here, so this module has no
dependency on mount or target/ephemeris state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits


@dataclass
class CaptureContext:
    """Everything needed to build a capture-time FITS header, beyond the
    pixel data itself. Fields with defaults are optional/best-effort --
    only EXPTIME is actually load-bearing for the rest of the pipeline
    (confirmed in DIRECT_HARDWARE_DESIGN.md), everything else is here for
    archival quality and header parity with NINA's output."""

    target_name: str
    exposure_seconds: float
    date_obs_utc: datetime
    frame_index: int = 0
    binning: int = 1
    gain: int = 0
    offset: int = 0
    electrons_per_adu: float | None = None
    pixel_size_um: float | None = None
    instrument_name: str = ""
    bayer_pattern: str | None = "RGGB"
    bayer_offset_x: int = 0
    bayer_offset_y: int = 0
    ccd_temperature_c: float | None = None
    set_temperature_c: float | None = None
    telescope_name: str = ""
    focal_length_mm: float | None = None
    focal_ratio: float | None = None
    ra_deg: float | None = None
    dec_deg: float | None = None
    site_lat_deg: float | None = None
    site_lon_deg: float | None = None
    site_elevation_m: float | None = None
    date_obs_local: datetime | None = None
    extra_header: dict[str, Any] = field(default_factory=dict)


def _filename_for(ctx: CaptureContext) -> str:
    """Matches NINA's naming convention exactly: {TARGET}_{DATETIME}__
    {EXPOSURE}s_{FRAME}.fits, e.g. ZTF109i_2025-12-20_20-28-04__102.00s_0000.fits
    -- file_poller.py's glob is loose ({target}_*.fits) but keeping this
    identical avoids surprising any tooling/archival browsing that expects
    the exact pattern."""
    ts = ctx.date_obs_utc.strftime("%Y-%m-%d_%H-%M-%S")
    return f"{ctx.target_name}_{ts}__{ctx.exposure_seconds:.2f}s_{ctx.frame_index:04d}.fits"


def build_header(ctx: CaptureContext, data: np.ndarray) -> fits.Header:
    header = fits.Header()
    header["IMAGETYP"] = ("LIGHT", "Type of exposure")
    header["EXPOSURE"] = (ctx.exposure_seconds, "[s] Exposure duration")
    header["EXPTIME"] = (ctx.exposure_seconds, "[s] Exposure duration")
    header["DATE-OBS"] = (
        ctx.date_obs_utc.strftime("%Y-%m-%dT%H:%M:%S.%f"),
        "Time of observation (UTC)",
    )
    if ctx.date_obs_local is not None:
        header["DATE-LOC"] = (
            ctx.date_obs_local.strftime("%Y-%m-%dT%H:%M:%S.%f"),
            "Time of observation (local)",
        )
    header["XBINNING"] = (ctx.binning, "X axis binning factor")
    header["YBINNING"] = (ctx.binning, "Y axis binning factor")
    header["GAIN"] = (ctx.gain, "Sensor gain")
    header["OFFSET"] = (ctx.offset, "Sensor gain offset")
    if ctx.electrons_per_adu is not None:
        header["EGAIN"] = (ctx.electrons_per_adu, "[e-/ADU] Electrons per A/D unit")
    if ctx.pixel_size_um is not None:
        header["XPIXSZ"] = (ctx.pixel_size_um, "[um] Pixel X axis size")
        header["YPIXSZ"] = (ctx.pixel_size_um, "[um] Pixel Y axis size")
    if ctx.instrument_name:
        header["INSTRUME"] = (ctx.instrument_name, "Imaging instrument name")
    if ctx.set_temperature_c is not None:
        header["SET-TEMP"] = (ctx.set_temperature_c, "[degC] CCD temperature setpoint")
    if ctx.ccd_temperature_c is not None:
        header["CCD-TEMP"] = (ctx.ccd_temperature_c, "[degC] CCD temperature")
    if ctx.bayer_pattern:
        header["BAYERPAT"] = (ctx.bayer_pattern, "Sensor Bayer pattern")
        header["XBAYROFF"] = (ctx.bayer_offset_x, "Bayer pattern X axis offset")
        header["YBAYROFF"] = (ctx.bayer_offset_y, "Bayer pattern Y axis offset")
    if ctx.telescope_name:
        header["TELESCOP"] = (ctx.telescope_name, "Name of telescope")
    if ctx.focal_length_mm is not None:
        header["FOCALLEN"] = (ctx.focal_length_mm, "[mm] Focal length")
    if ctx.focal_ratio is not None:
        header["FOCRATIO"] = (ctx.focal_ratio, "Focal ratio")
    if ctx.ra_deg is not None:
        header["RA"] = (ctx.ra_deg, "[deg] RA of telescope")
    if ctx.dec_deg is not None:
        header["DEC"] = (ctx.dec_deg, "[deg] Declination of telescope")
    if ctx.site_elevation_m is not None:
        header["SITEELEV"] = (ctx.site_elevation_m, "[m] Observation site elevation")
    if ctx.site_lat_deg is not None:
        header["SITELAT"] = (ctx.site_lat_deg, "[deg] Observation site latitude")
    if ctx.site_lon_deg is not None:
        header["SITELONG"] = (ctx.site_lon_deg, "[deg] Observation site longitude")
    header["OBJECT"] = (ctx.target_name, "Name of the object of interest")
    header["ROWORDER"] = ("TOP-DOWN", "FITS Image Orientation")
    header["EQUINOX"] = (2000.0, "Equinox of celestial coordinate system")
    header["SWCREATE"] = ("ASTRO-NEO direct (Alpaca)", "Software that created this file")

    for key, value in ctx.extra_header.items():
        header[key] = value

    return header


def write_capture_fits(
    data: np.ndarray,
    ctx: CaptureContext,
    output_root: Path | str,
) -> Path:
    """Write `data` (as produced by AlpacaCameraClient.get_image_array --
    raw sensor values, no debayering, (height, width) shape) to a FITS
    file at the same directory convention NINA uses:
    {output_root}/{date}/{target}/SNAPSHOT/{filename}, using the exposure
    start time's UTC date for the {date} component (matching NINA/
    file_poller's own local-date convention closely enough -- see
    _filename_for's docstring for the one loosely-related edge case).
    """
    date_dir = ctx.date_obs_utc.date().isoformat()
    snapshot_dir = Path(output_root) / date_dir / ctx.target_name / "SNAPSHOT"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    filename = _filename_for(ctx)
    path = snapshot_dir / filename

    header = build_header(ctx, data)
    # BZERO/BSCALE handling for unsigned 16-bit data is automatic in
    # astropy when the array dtype is uint16 -- matches NINA's own
    # BZERO=32768 convention for representing unsigned values in FITS's
    # signed BITPIX=16.
    hdu = fits.PrimaryHDU(data=data, header=header)
    hdu.writeto(path, overwrite=True)
    return path


__all__ = ["CaptureContext", "write_capture_fits", "build_header"]
