"""Analysis service for source detection and association."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, List, Optional, Tuple

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats
from astropy.wcs import WCS
from photutils.detection import DAOStarFinder
from sqlmodel import Session, select

from app.models import CaptureLog, NeoEphemeris, CandidateAssociation


class AnalysisService:
    def __init__(self, session: Session | None = None) -> None:
        self.session = session

    def detect_sources(self, path: Path, wcs: WCS | None = None) -> List[dict[str, Any]]:
        """Detect all sources in the image and return their centroids and properties."""
        try:
            data = fits.getdata(path)
        except Exception:
            return []
            
        if data is None:
            return []
            
        data = np.asarray(data, dtype=float)
        mean, median, std = sigma_clipped_stats(data, sigma=3.0)
        threshold = median + (5.0 * std)
        
        try:
            # FWHM=4.0 is a reasonable default for typical seeing
            finder = DAOStarFinder(fwhm=4.0, threshold=threshold - median)
            sources = finder(data - median)
        except Exception:
            return []
            
        if sources is None or len(sources) == 0:
            return []
            
        results = []
        for source in sources:
            x = float(source["xcentroid"])
            y = float(source["ycentroid"])
            flux = float(source["flux"])
            peak = float(source["peak"])
            snr = float(peak / std) if std else 0.0
            
            ra_deg = None
            dec_deg = None
            
            if wcs:
                # Convert pixel to sky coordinates
                sky = wcs.pixel_to_world(x, y)
                ra_deg = float(sky.ra.deg)
                dec_deg = float(sky.dec.deg)
                
            results.append({
                "x": x,
                "y": y,
                "flux": flux,
                "peak": peak,
                "snr": snr,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
            })
            
        return results

    def find_best_match(
        self, 
        detections: List[dict[str, Any]], 
        predicted_ra: float, 
        predicted_dec: float, 
        tolerance_arcsec: float = 5.0
    ) -> Optional[dict[str, Any]]:
        """Find the detection closest to the predicted position within tolerance."""
        best_match = None
        min_dist = float("inf")
        
        for det in detections:
            ra = det.get("ra_deg")
            dec = det.get("dec_deg")
            if ra is None or dec is None:
                continue
                
            # Simple Euclidean distance for small separations (sufficient for matching)
            # Correct for cos(dec)
            cos_dec = math.cos(math.radians(predicted_dec))
            d_ra = (ra - predicted_ra) * cos_dec
            d_dec = dec - predicted_dec
            dist_deg = math.sqrt(d_ra**2 + d_dec**2)
            dist_arcsec = dist_deg * 3600.0
            
            if dist_arcsec < tolerance_arcsec and dist_arcsec < min_dist:
                min_dist = dist_arcsec
                best_match = det
                
        return best_match

    def auto_associate(self, db: Session, capture: CaptureLog, wcs: WCS) -> Optional[CandidateAssociation]:
        """Attempt to automatically associate a capture with its target ephemeris."""
        if not capture.target or capture.target == "unknown":
            return None
            
        # 1. Find Ephemeris
        # Look for ephemeris close to capture time
        # This assumes we have ephemeris populated. 
        # For now, we'll just look for ANY ephemeris for this target that is close in time.
        # Ideally, we interpolate, but nearest neighbor is fine for this step.
        
        # We need to join with NeoCandidate to get the ID, but for now let's assume 
        # we can find it via trksub if we had that link.
        # Actually, CaptureLog.target is usually the trksub/designation.
        # Let's try to find an ephemeris entry.
        
        # Find candidate first
        # We don't have a direct link from CaptureLog to NeoCandidate easily without querying.
        # Let's assume target name matches trksub.
        
        # Note: This query might be expensive if not indexed well, but for now it's okay.
        # We need to find the ephemeris that corresponds to this capture time.
        
        # Simplified: Find nearest ephemeris within 5 minutes
        stmt = select(NeoEphemeris).where(NeoEphemeris.trksub == capture.target).order_by(
            (NeoEphemeris.epoch - capture.started_at)
        ) # This sorting might not work directly in all SQL dialects with abs()
        
        # Better: fetch all for target and find nearest in python (dataset is small per target usually)
        ephems = db.exec(select(NeoEphemeris).where(NeoEphemeris.trksub == capture.target)).all()
        
        if not ephems:
            return None
            
        best_eph = None
        min_diff = float("inf")
        for eph in ephems:
            diff = abs((eph.epoch - capture.started_at).total_seconds())
            if diff < min_diff:
                min_diff = diff
                best_eph = eph
                
        if not best_eph or min_diff > 300: # > 5 mins
            return None
            
        # 2. Detect Sources
        detections = self.detect_sources(Path(capture.path), wcs)
        
        # 3. Match
        match = self.find_best_match(detections, best_eph.ra_deg, best_eph.dec_deg, tolerance_arcsec=10.0) # 10 arcsec tolerance
        
        if match:
            # 4. Create Association
            assoc = CandidateAssociation(
                capture_id=capture.id,
                ra_deg=match["ra_deg"],
                dec_deg=match["dec_deg"],
                # We could store residuals here if the model supported it
            )
            db.add(assoc)
            db.commit()
            db.refresh(assoc)
            return assoc
            
        return None
    def resolve_click(self, capture: CaptureLog, click_x: float | None = None, click_y: float | None = None, polygon: list[dict[str, float]] | None = None, crop_size: int = 20) -> dict[str, Any] | None:
        """Resolve a click or polygon on an image to a precise centroid and RA/Dec."""
        import logging
        path = Path(capture.path)
        if not path.exists():
            logging.error(f"File not found: {path}")
            return None
            
        try:
            # Load data
            with fits.open(path) as hdul:
                data = hdul[0].data
                if data is None:
                    return None
                data = np.asarray(data, dtype=float)
                
            h, w = data.shape
            
            if polygon:
                # Polygon mode
                from photutils.aperture import PolygonAperture
                
                # Convert list of dicts to list of tuples
                vertices = [(p["x"], p["y"]) for p in polygon]
                logging.info(f"Polygon vertices: {vertices}")
                aperture = PolygonAperture(vertices)
                
                # Create a mask from the aperture
                mask_obj = aperture.to_mask(method='center')
                if mask_obj is None:
                     logging.error("Failed to create mask from polygon")
                     return None
                     
                # Better: use the weighted data
                weighted_data = mask_obj.multiply(data)
                if weighted_data is None:
                    logging.error("Failed to apply mask to data")
                    return None
                    
                # Find max index in the cutout
                # Check if weighted_data has any non-zero values
                if np.all(weighted_data == 0):
                    logging.warning("Polygon mask resulted in all zeros (empty intersection?)")
                    # Try 'exact' or 'subpixel' method if center fails?
                    # Or just return None
                    return None
                    
                y_local, x_local = np.unravel_index(np.argmax(weighted_data), weighted_data.shape)
                
                # Convert to global coordinates
                # mask_obj.bbox gives (ymin, xmin, ymax, xmax)
                ymin, xmin, ymax, xmax = mask_obj.bbox.ymin, mask_obj.bbox.xmin, mask_obj.bbox.ymax, mask_obj.bbox.xmax
                
                global_x = xmin + x_local
                global_y = ymin + y_local
                
                logging.info(f"Polygon max at global: {global_x}, {global_y}")
                
                # Refine with centroiding around this peak
                x_int, y_int = int(global_x), int(global_y)
                
                # Reuse the crop logic below
                x_start = max(0, x_int - crop_size)
                x_end = min(w, x_int + crop_size)
                y_start = max(0, y_int - crop_size)
                y_end = min(h, y_int + crop_size)
                
                crop = data[y_start:y_end, x_start:x_end]
                
            elif click_x is not None and click_y is not None:
                # Click mode
                x_int, y_int = int(round(click_x)), int(round(click_y))
                
                x_start = max(0, x_int - crop_size)
                x_end = min(w, x_int + crop_size)
                y_start = max(0, y_int - crop_size)
                y_end = min(h, y_int + crop_size)
                
                crop = data[y_start:y_end, x_start:x_end]
            else:
                return None
                
            if crop.size == 0:
                logging.error("Crop is empty")
                return None
                
            # Find centroid in crop
            mean, median, std = sigma_clipped_stats(crop, sigma=3.0)
            threshold = median + (3.0 * std) # Lower threshold for manual clicks
            
            finder = DAOStarFinder(fwhm=4.0, threshold=threshold - median)
            sources = finder(crop - median)
            
            if sources is None or len(sources) == 0:
                # Fallback: just use the brightest pixel in crop
                y_max, x_max = np.unravel_index(np.argmax(crop), crop.shape)
                local_x, local_y = float(x_max), float(y_max)
                peak = float(crop[y_max, x_max])
                snr = (peak - median) / std if std else 0.0
            else:
                # Find source closest to center of crop
                cx, cy = crop.shape[1] / 2, crop.shape[0] / 2
                best_dist = float("inf")
                best_source = None
                
                for source in sources:
                    sx, sy = source["xcentroid"], source["ycentroid"]
                    dist = (sx - cx)**2 + (sy - cy)**2
                    if dist < best_dist:
                        best_dist = dist
                        best_source = source
                        
                if best_source:
                    local_x = float(best_source["xcentroid"])
                    local_y = float(best_source["ycentroid"])
                    peak = float(best_source["peak"])
                    snr = float(peak / std) if std else 0.0
                else:
                    return None

            # Convert back to global image coordinates
            global_x = x_start + local_x
            global_y = y_start + local_y
            
            # Get WCS
            wcs_path = path.with_suffix(".wcs")
            if wcs_path.exists():
                wcs = WCS(str(wcs_path))
                sky = wcs.pixel_to_world(global_x, global_y)
                ra_deg = float(sky.ra.deg)
                dec_deg = float(sky.dec.deg)
            else:
                logging.error("WCS file not found")
                return None
                
            return {
                "x": global_x,
                "y": global_y,
                "ra_deg": ra_deg,
                "dec_deg": dec_deg,
                "snr": snr,
                "peak": peak
            }
            
        except Exception as exc:
            logging.exception(f"Error resolving click: {exc}")
            return None
