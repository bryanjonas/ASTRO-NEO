import httpx
import json
import time

def test_connect_and_capture():
    base_url = "http://localhost:8001/api"
    
    # 1. Connect Camera
    print("Connecting camera...")
    try:
        resp = httpx.get(f"{base_url}/equipment/camera/connect", timeout=10.0)
        print(f"Connect Status: {resp.status_code}")
        print(f"Connect Body: {resp.text}")
    except Exception as e:
        print(f"Connect failed: {e}")
        return

    # 2. Capture
    url = f"{base_url}/equipment/camera/capture"
    params = {
        "duration": 0.1,
        "binning": 1,
        "download": "true"
    }
    
    print(f"\nSending capture request to {url} with params {params}")
    
    try:
        response = httpx.get(url, params=params, timeout=30.0)
        print(f"\nCapture Status Code: {response.status_code}")
        try:
            data = response.json()
            print("Capture Response Body:")
            print(json.dumps(data, indent=2))
        except:
            print(f"Capture Response Text: {response.text}")
    except Exception as e:
        print(f"\nCapture Error: {e}")

if __name__ == "__main__":
    test_connect_and_capture()
