import httpx
import json

def test_wrapped():
    url = "http://MELE:1888/v2/api/sequence/load"
    
    # Payload wrapped in Response object
    payload = {
        "Response": [
            {
                "$type": "NINA.Sequencer.Container.SequenceContainer, NINA.Sequencer",
                "Name": "Start_Container",
                "Items": [],
                "Triggers": [],
                "Conditions": []
            }
        ]
    }
    
    print("Test Wrapped in Response object")
    print(json.dumps(payload, indent=2))
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        print(f"Status: {response.status_code}, Body: {response.text}\n")
    except Exception as e:
        print(f"Error: {e}\n")

if __name__ == "__main__":
    test_wrapped()
