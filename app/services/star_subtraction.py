"""Star subtraction using astrometry.net catalog correlation data and Gaia."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.coordinates import SkyCoord
import astropy.units as u

logger = logging.getLogger(__name__)

# Default magnitude limit for Gaia query (fainter = more stars but slower)
DEFAULT_GAIA_MAG_LIMIT = 18.0


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

    def get_catalog_stars(self, use_gaia: bool = False, mag_limit: float = DEFAULT_GAIA_MAG_LIMIT) -> list[dict[str, Any]]:
        """
        Extract catalog star positions from .corr file, optionally supplemented by Gaia.

        Args:
            use_gaia: If True, query Gaia for additional stars beyond .corr file
            mag_limit: Magnitude limit for Gaia query (default 18.0)

        Returns:
            List of star dicts with x, y, ra, dec (and optionally mag)
        """
        stars = []

        # First, get stars from .corr file (astrometry.net matches)
        corr_stars = self._get_corr_stars()
        stars.extend(corr_stars)
        corr_count = len(corr_stars)

        # Optionally query Gaia for more stars
        if use_gaia:
            gaia_stars = self._query_gaia_stars(mag_limit=mag_limit)
            if gaia_stars:
                # Merge, avoiding duplicates (within 3 arcsec)
                merged = self._merge_star_lists(stars, gaia_stars, match_radius_arcsec=3.0)
                new_count = len(merged) - len(stars)
                stars = merged
                logger.info(f"Added {new_count} Gaia stars (mag<{mag_limit}) to {corr_count} .corr stars = {len(stars)} total")

        if not stars:
            logger.debug("No catalog stars found")

        return stars

    def _get_corr_stars(self) -> list[dict[str, Any]]:
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
                        'source': 'corr',
                    })

                logger.info(f"Loaded {len(stars)} catalog stars from {self.corr_path.name}")
                return stars

        except Exception as e:
            logger.warning(f"Could not read .corr file: {e}")
            return []

    def _query_gaia_stars(self, mag_limit: float = DEFAULT_GAIA_MAG_LIMIT) -> list[dict[str, Any]]:
        """
        Query Gaia DR3 for stars in the field of view.

        Returns stars with pixel positions computed from WCS.
        """
        if not self.wcs_path.exists():
            logger.debug("No WCS file, cannot query Gaia")
            return []

        try:
            from astroquery.gaia import Gaia
            Gaia.MAIN_GAIA_TABLE = "gaiadr3.gaia_source"
            Gaia.ROW_LIMIT = 10000  # Reasonable limit
        except ImportError:
            logger.warning("astroquery not available, cannot query Gaia")
            return []

        try:
            wcs = WCS(str(self.wcs_path))

            # Get image dimensions from WCS
            naxis1 = wcs.pixel_shape[0] if wcs.pixel_shape else 4000
            naxis2 = wcs.pixel_shape[1] if wcs.pixel_shape else 4000

            # Get field center and size
            center_x, center_y = naxis1 / 2, naxis2 / 2
            center_sky = wcs.pixel_to_world(center_x, center_y)
            center_ra = center_sky.ra.deg
            center_dec = center_sky.dec.deg

            # Estimate field radius (diagonal / 2)
            pixel_scale = self._get_pixel_scale(wcs)
            if pixel_scale == 0:
                pixel_scale = 2.4  # Default fallback
            diagonal_px = np.sqrt(naxis1**2 + naxis2**2)
            field_radius_arcsec = (diagonal_px / 2) * pixel_scale
            field_radius_deg = field_radius_arcsec / 3600.0

            logger.debug(
                f"Querying Gaia: center=({center_ra:.4f}, {center_dec:.4f}) "
                f"radius={field_radius_deg:.3f}° mag<{mag_limit}"
            )

            # Query Gaia
            query = f"""
            SELECT ra, dec, phot_g_mean_mag
            FROM gaiadr3.gaia_source
            WHERE 1=CONTAINS(
                POINT('ICRS', ra, dec),
                CIRCLE('ICRS', {center_ra}, {center_dec}, {field_radius_deg})
            )
            AND phot_g_mean_mag < {mag_limit}
            ORDER BY phot_g_mean_mag ASC
            """

            job = Gaia.launch_job_async(query, verbose=False)
            result = job.get_results()

            if result is None or len(result) == 0:
                logger.debug("Gaia query returned no results")
                return []

            # Convert to pixel coordinates
            stars = []
            for row in result:
                ra = float(row['ra'])
                dec = float(row['dec'])
                mag = float(row['phot_g_mean_mag'])

                try:
                    x, y = wcs.world_to_pixel_values(ra, dec)
                    # Only include stars within image bounds
                    if 0 <= x < naxis1 and 0 <= y < naxis2:
                        stars.append({
                            'x': float(x),
                            'y': float(y),
                            'ra': ra,
                            'dec': dec,
                            'mag': mag,
                            'source': 'gaia',
                        })
                except Exception:
                    continue

            logger.info(f"Gaia query returned {len(stars)} stars in field (mag<{mag_limit})")
            return stars

        except Exception as e:
            logger.warning(f"Gaia query failed: {e}")
            return []

    def _merge_star_lists(
        self,
        list1: list[dict[str, Any]],
        list2: list[dict[str, Any]],
        match_radius_arcsec: float = 3.0
    ) -> list[dict[str, Any]]:
        """Merge two star lists, avoiding duplicates within match radius."""
        if not list1:
            return list2
        if not list2:
            return list1

        # Build coordinate arrays for list1
        ra1 = np.array([s['ra'] for s in list1])
        dec1 = np.array([s['dec'] for s in list1])

        merged = list(list1)  # Start with all of list1

        for star in list2:
            ra2, dec2 = star['ra'], star['dec']
            # Check distance to all stars in list1
            cos_dec = np.cos(np.radians(dec2))
            d_ra = (ra1 - ra2) * cos_dec
            d_dec = dec1 - dec2
            dist_arcsec = np.sqrt(d_ra**2 + d_dec**2) * 3600.0

            # Only add if no match within radius
            if np.all(dist_arcsec > match_radius_arcsec):
                merged.append(star)

        return merged

    def subtract_stars(
        self,
        data: np.ndarray,
        target_ra: float | None = None,
        target_dec: float | None = None,
        exclusion_radius_arcsec: float = 0.0,
        star_fwhm_px: float = 4.0,
        use_gaia: bool = False,
        gaia_mag_limit: float = DEFAULT_GAIA_MAG_LIMIT,
    ) -> tuple[np.ndarray, int]:
        """
        Subtract catalog stars from image.

        Args:
            data: Image data array
            target_ra: Optional target RA in degrees (for exclusion zone)
            target_dec: Optional target Dec in degrees (for exclusion zone)
            exclusion_radius_arcsec: Don't subtract within this radius of target (default 0 = no exclusion)
            star_fwhm_px: FWHM of stars in pixels (for Gaussian model)
            use_gaia: If True, query Gaia for additional stars beyond .corr file
            gaia_mag_limit: Magnitude limit for Gaia query (default 18.0)

        Returns:
            Tuple of (cleaned image data, number of stars subtracted)

        Note:
            When target coordinates are None or exclusion_radius is 0, ALL catalog
            stars are subtracted. This is the proper workflow for blind detection
            where ephemeris predictions should not influence what is detected.
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

        # Get catalog stars (optionally supplemented by Gaia)
        catalog_stars = self.get_catalog_stars(use_gaia=use_gaia, mag_limit=gaia_mag_limit)
        if not catalog_stars:
            logger.debug("No catalog stars to subtract")
            return data, 0

        # Convert target to pixels and calculate exclusion zone (only if target provided)
        target_x, target_y = None, None
        exclusion_radius_px = 0.0

        if target_ra is not None and target_dec is not None and exclusion_radius_arcsec > 0:
            try:
                target_x, target_y = wcs.world_to_pixel_values(target_ra, target_dec)
                pixel_scale = self._get_pixel_scale(wcs)
                if pixel_scale == 0:
                    logger.warning("Could not determine pixel scale, using default exclusion")
                    exclusion_radius_px = 50  # Default fallback
                else:
                    exclusion_radius_px = exclusion_radius_arcsec / pixel_scale
                logger.debug(
                    f"Using exclusion zone: {exclusion_radius_arcsec:.1f}\" around "
                    f"({target_ra:.5f}, {target_dec:.5f})"
                )
            except Exception as e:
                logger.warning(f"Failed to convert target position: {e}. Proceeding without exclusion zone.")
                target_x, target_y = None, None
                exclusion_radius_px = 0.0

        # Subtract each star
        subtracted = data.copy()
        stars_subtracted = 0

        for star in catalog_stars:
            x, y = star['x'], star['y']

            # Skip if near target (only if exclusion zone is active)
            if target_x is not None and target_y is not None and exclusion_radius_px > 0:
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
