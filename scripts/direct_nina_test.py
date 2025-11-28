import httpx
import json
import sys
import os

# Add the project root to sys.path so we can import nina_bridge
sys.path.append("/app")

from nina_bridge.sequence_builder import build_nina_sequence

def test_direct_load():
    # Build the sequence
    sequence = build_nina_sequence(
        name="Direct Test",
        target="Test Target",
        count=1,
        filter_name="L",
        binning=1,
        exposure_seconds=1.0
    )
    
    url = "http://MELE:1888/v2/api/sequence/load"
    print(f"Sending request to {url}")
    print("Payload:")
    print(json.dumps(sequence, indent=2))
    
    try:
        response = httpx.post(url, json=sequence, timeout=10.0)
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response Body: {response.text}")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    test_direct_load()
