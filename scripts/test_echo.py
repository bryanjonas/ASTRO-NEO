import httpx
import json

def test_echo():
    base_url = "http://mele:1888/v2/api"
    
    # 1. Fetch current sequence
    print("Fetching current sequence...")
    resp = httpx.get(f"{base_url}/sequence/json")
    if resp.status_code != 200:
        print(f"Failed to fetch: {resp.status_code}")
        return
        
    data = resp.json()
    sequence = data.get("Response")
    
    # 2. Clean up (remove Status fields if any)
    # Recursively remove 'Status' keys
    def clean(obj):
        if isinstance(obj, dict):
            if "Status" in obj:
                del obj["Status"]
            for k, v in obj.items():
                clean(v)
        elif isinstance(obj, list):
            for item in obj:
                clean(item)
    
    clean(sequence)
    
    print("Sending cleaned sequence back...")
    print(json.dumps(sequence, indent=2))
    
    # 3. Send it back
    resp = httpx.post(f"{base_url}/sequence/load", json=sequence)
    print(f"\nStatus Code: {resp.status_code}")
    print(f"Response Body: {resp.text}")

if __name__ == "__main__":
    test_echo()
