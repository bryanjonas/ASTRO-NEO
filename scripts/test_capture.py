import httpx
import json

def test_capture_download():
    url = "http://localhost:8001/api/equipment/camera/capture"
    params = {
        "duration": 0.1,
        "binning": 1,
        "download": "true"
    }
    
    print(f"Sending capture request to {url} with params {params}")
    
    try:
        response = httpx.get(url, params=params, timeout=30.0)
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
    test_capture_download()
