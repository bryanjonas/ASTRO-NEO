import httpx
import json

def test_empty_list():
    url = "http://MELE:1888/v2/api/sequence/load"
    payload = []
    print(f"Sending request to {url}")
    print("Payload:")
    print(json.dumps(payload, indent=2))
    
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        print(f"\nStatus Code: {response.status_code}")
        print(f"Response Body: {response.text}")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    test_empty_list()
