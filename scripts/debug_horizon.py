import asyncio
import httpx
import json

PVGIS_API_URL = "https://re.jrc.ec.europa.eu/api/v5_2/printhorizon"

async def fetch_horizon():
    # TODO: Read from config/site_local.yml instead of hardcoding
    lat = 51.4769  # Example: Greenwich Observatory
    lon = -0.0005
    params = {
        "lat": lat,
        "lon": lon,
        "outputformat": "json",
    }
    print(f"Fetching horizon for {lat}, {lon}...")
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(PVGIS_API_URL, params=params, timeout=30.0)
            print(f"Status Code: {response.status_code}")
            if response.status_code != 200:
                print(f"Error Response: {response.text}")
                return

            data = response.json()
            print("Raw Response Keys:", data.keys())
            
            outputs = data.get("outputs", {})
            horizon_profile = outputs.get("horizon_profile", [])
            print(f"Horizon Profile Points: {len(horizon_profile)}")
            if len(horizon_profile) > 0:
                print("First 5 points:", horizon_profile[:5])
                
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(fetch_horizon())
