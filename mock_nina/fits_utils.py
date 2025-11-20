"""Utilities for creating dummy FITS files."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits


def create_dummy_fits(
    path: Path,
    exposure_seconds: float,
    filter_name: str,
    binning: int,
    width: int = 100,
    height: int = 100,
) -> Path:
    """Write a small FITS image with synthetic pixel data."""

    data = (np.random.rand(height, width) * 2000 + 1000).astype(np.uint16)
    hdu = fits.PrimaryHDU(data=data)
    timestamp = datetime.now(timezone.utc)
    hdu.header["SIMPLE"] = True
    hdu.header["BITPIX"] = 16
    hdu.header["NAXIS"] = 2
    hdu.header["NAXIS1"] = width
    hdu.header["NAXIS2"] = height
    hdu.header["EXPTIME"] = exposure_seconds
    hdu.header["FILTER"] = filter_name
    hdu.header["XBINNING"] = binning
    hdu.header["YBINNING"] = binning
    hdu.header["DATE-OBS"] = timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    hdul = fits.HDUList([hdu])
    path.parent.mkdir(parents=True, exist_ok=True)
    hdul.writeto(path, overwrite=True)
    return path


__all__ = ["create_dummy_fits"]
