import httpx
import json

def list_sequences():
    url = "http://mele:1888/v2/api/sequence/list-available"
    print(f"Fetching sequences from {url}")
    
    try:
        response = httpx.get(url, timeout=10.0)
        print(f"\nStatus Code: {response.status_code}")
        try:
            data = response.json()
            print("Response Body:")
            print(json.dumps(data, indent=2))
        except:
            print(f"Response Text: {response.text}")
    except Exception as e:
        print(f"\nError: {e}")

if __name__ == "__main__":
    list_sequences()
