"""Star subtraction using astrometry.net catalog correlation data."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

logger = logging.getLogger(__name__)


class CatalogStarSubtractor:
    """
    Subtract field stars using astrometry.net correlation file.

    The .corr file contains matched catalog stars from the solve,
    so we don't need to query any external catalogs.
    """

    def __init__(self, fits_path: Path):
        self.fits_path = Path(fits_path)
        self.corr_path = self.fits_path.with_suffix('.corr')
        self.wcs_path = self.fits_path.with_suffix('.wcs')

    def get_catalog_stars(self) -> list[dict[str, Any]]:
        """Extract catalog star positions from .corr file."""
        if not self.corr_path.exists():
            logger.debug(f"No .corr file found at {self.corr_path}")
            return []

        try:
            with fits.open(self.corr_path) as hdul:
                if len(hdul) < 2:
                    logger.warning(".corr file has no binary table extension")
                    return []

                data = hdul[1].data
                if data is None or len(data) == 0:
                    return []

                stars = []

                for row in data:
                    # field_x, field_y are the pixel positions
                    # index_ra, index_dec are the catalog coordinates
                    stars.append({
                        'x': float(row['field_x']),
                        'y': float(row['field_y']),
                        'ra': float(row['index_ra']),
                        'dec': float(row['index_dec']),
                    })

                logger.info(f"Loaded {len(stars)} catalog stars from {self.corr_path.name}")
                return stars

        except Exception as e:
            logger.warning(f"Could not read .corr file: {e}")
            return []

    def subtract_stars(
        self,
        data: np.ndarray,
        target_ra: float,
        target_dec: float,
        exclusion_radius_arcsec: float = 20.0,
        star_fwhm_px: float = 4.0
    ) -> tuple[np.ndarray, int]:
        """
        Subtract catalog stars from image.

        Args:
            data: Image data array
            target_ra: Target RA in degrees
            target_dec: Target Dec in degrees
            exclusion_radius_arcsec: Don't subtract within this radius of target
            star_fwhm_px: FWHM of stars in pixels (for Gaussian model)

        Returns:
            Tuple of (cleaned image data, number of stars subtracted)
        """
        # Load WCS
        if not self.wcs_path.exists():
            logger.warning(f"No WCS file found at {self.wcs_path}, cannot subtract stars")
            return data, 0

        try:
            wcs = WCS(str(self.wcs_path))
        except Exception as e:
            logger.error(f"Failed to load WCS: {e}")
            return data, 0

        # Get catalog stars
        catalog_stars = self.get_catalog_stars()
        if not catalog_stars:
            logger.debug("No catalog stars to subtract")
            return data, 0

        # Convert target to pixels
        try:
            target_x, target_y = wcs.world_to_pixel_values(target_ra, target_dec)
        except Exception as e:
            logger.error(f"Failed to convert target position to pixels: {e}")
            return data, 0

        # Get pixel scale
        pixel_scale = self._get_pixel_scale(wcs)
        if pixel_scale == 0:
            logger.warning("Could not determine pixel scale, using default exclusion")
            exclusion_radius_px = 50  # Default fallback
        else:
            exclusion_radius_px = exclusion_radius_arcsec / pixel_scale

        # Subtract each star
        subtracted = data.copy()
        stars_subtracted = 0

        for star in catalog_stars:
            x, y = star['x'], star['y']

            # Skip if near target
            dist = np.sqrt((x - target_x)**2 + (y - target_y)**2)
            if dist < exclusion_radius_px:
                logger.debug(f"Skipping star at ({x:.1f}, {y:.1f}) - too close to target (dist={dist:.1f}px)")
                continue

            # Skip if star is outside image bounds
            if not (0 <= x < data.shape[1] and 0 <= y < data.shape[0]):
                continue

            # Measure flux in small aperture around star
            flux = self._measure_flux(data, x, y, radius=star_fwhm_px * 2)

            if flux <= 0:
                continue

            # Subtract Gaussian model
            subtracted = self._subtract_gaussian(
                subtracted, x, y, star_fwhm_px, flux
            )
            stars_subtracted += 1

        logger.info(f"Subtracted {stars_subtracted}/{len(catalog_stars)} catalog stars")
        return subtracted, stars_subtracted

    def _get_pixel_scale(self, wcs: WCS) -> float:
        """Get pixel scale in arcsec/pixel."""
        try:
            # Try to get pixel scale from CD matrix
            cd = wcs.pixel_scale_matrix
            scale_deg = float(np.sqrt(np.abs(np.linalg.det(cd))))
            return scale_deg * 3600.0  # Convert to arcsec
        except Exception:
            # Fallback: try CDELT keywords
            try:
                cdelt1 = wcs.wcs.cdelt[0]
                cdelt2 = wcs.wcs.cdelt[1]
                scale_deg = float(np.sqrt(abs(cdelt1 * cdelt2)))
                return scale_deg * 3600.0
            except Exception:
                logger.warning("Could not determine pixel scale from WCS")
                return 0.0

    def _measure_flux(self, data: np.ndarray, x: float, y: float, radius: float) -> float:
        """Measure total flux in circular aperture."""
        h, w = data.shape

        # Create circular aperture mask
        y_indices, x_indices = np.ogrid[:h, :w]
        distances = np.sqrt((x_indices - x)**2 + (y_indices - y)**2)
        mask = distances <= radius

        # Measure flux
        flux = float(np.sum(data[mask]))
        return flux

    def _subtract_gaussian(
        self,
        data: np.ndarray,
        x: float,
        y: float,
        fwhm: float,
        flux: float
    ) -> np.ndarray:
        """Subtract a 2D Gaussian from the image."""
        sigma = fwhm / 2.355  # Convert FWHM to sigma

        # Only subtract in local region for efficiency
        h, w = data.shape
        size = int(fwhm * 5)  # 5-sigma radius

        x_min = max(0, int(x) - size)
        x_max = min(w, int(x) + size + 1)
        y_min = max(0, int(y) - size)
        y_max = min(h, int(y) + size + 1)

        if x_min >= x_max or y_min >= y_max:
            return data

        # Create meshgrid for local region
        y_grid, x_grid = np.ogrid[y_min:y_max, x_min:x_max]

        # 2D Gaussian
        r2 = (x_grid - x)**2 + (y_grid - y)**2
        normalization = 2 * np.pi * sigma**2
        gaussian = (flux / normalization) * np.exp(-r2 / (2 * sigma**2))

        # Subtract from local region
        result = data.copy()
        result[y_min:y_max, x_min:x_max] -= gaussian

        return result


__all__ = ["CatalogStarSubtractor"]
