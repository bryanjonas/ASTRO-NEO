"""Horizon retrieval service using PVGIS API."""

import json
import logging
from typing import Any, List, Dict

import httpx

logger = logging.getLogger(__name__)

PVGIS_API_URL = "https://re.jrc.ec.europa.eu/api/v5_2/printhorizon"


async def fetch_horizon_profile(lat: float, lon: float) -> List[Dict[str, float]]:
    """
    Fetch horizon profile from PVGIS.
    
    Returns a list of dicts with 'az' and 'alt' keys (degrees).
    """
    params = {
        "lat": lat,
        "lon": lon,
        "outputformat": "json",
    }
    
    logger.info("Fetching horizon profile from PVGIS for lat=%s, lon=%s", lat, lon)
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(PVGIS_API_URL, params=params, timeout=30.0)
            response.raise_for_status()
            data = response.json()
            
            # Parse response
            # The structure based on docs/examples usually has inputs, outputs, meta.
            # We need to find where the horizon points are.
            # Typically: data['outputs']['horizon_profile'] which is a list of {A: azimuth, H: height}
            
            outputs = data.get("outputs", {})
            horizon_profile = outputs.get("horizon_profile", [])
            
            if not horizon_profile:
                logger.warning("PVGIS returned no horizon profile data: %s", data)
                return []
                
            # Convert to our format: list of {"az": ..., "alt": ...}
            # PVGIS returns 'A' for azimuth and 'H' for horizon height.
            result = []
            for point in horizon_profile:
                az = point.get("A")
                alt = point.get("H")
                if az is not None and alt is not None:
                    result.append({"az": float(az), "alt": float(alt)})
            
            if len(result) < 10:
                logger.warning("Suspiciously low horizon point count: %d", len(result))
                logger.debug("Raw PVGIS response: %s", json.dumps(data))
            
            logger.info("Successfully fetched %d horizon points", len(result))
            return result
            
        except httpx.HTTPError as exc:
            logger.error("PVGIS API error: %s", exc, exc_info=True)
            if hasattr(exc, "response") and exc.response:
                logger.error("PVGIS error response: %s %s", exc.response.status_code, exc.response.text[:500])
            raise
        except Exception as exc:
            logger.error("Error parsing PVGIS response: %s", exc, exc_info=True)
            raise
