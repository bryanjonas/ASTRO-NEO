import httpx
import json

def test_list_and_connect():
    base_url = "http://localhost:8001/api"
    
    # 1. List Devices
    print("Listing camera devices...")
    try:
        resp = httpx.get(f"{base_url}/equipment/camera/list-devices", timeout=10.0)
        print(f"List Status: {resp.status_code}")
        try:
            data = resp.json()
            print("List Response:")
            print(json.dumps(data, indent=2))
            
            devices = data.get("Response", [])
            if devices:
                first_device = devices[0].get("Id")
                print(f"\nFound device: {first_device}")
                
                # 2. Connect to specific device
                print(f"Connecting to {first_device}...")
                resp = httpx.get(f"{base_url}/equipment/camera/connect", params={"to": first_device}, timeout=10.0)
                print(f"Connect Status: {resp.status_code}")
                print(f"Connect Body: {resp.text}")
            else:
                print("\nNo devices found to connect to.")
                
        except Exception as e:
            print(f"Error parsing list response: {e}")
            print(f"Body: {resp.text}")
            
    except Exception as e:
        print(f"List failed: {e}")

if __name__ == "__main__":
    test_list_and_connect()
