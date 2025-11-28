import httpx
import json

def test_incremental():
    url = "http://MELE:1888/v2/api/sequence/load"
    
    # Test 1: Just Start Container with Type
    payload = [
        {
            "$type": "NINA.Sequencer.Container.SequenceContainer, NINA.Sequencer",
            "Name": "Start_Container",
            "Items": [],
            "Triggers": [],
            "Conditions": []
        }
    ]
    
    print("Test 1: Start Container with Type")
    print(json.dumps(payload, indent=2))
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        print(f"Status: {response.status_code}, Body: {response.text}\n")
    except Exception as e:
        print(f"Error: {e}\n")

    # Test 2: Global Triggers wrapper (no type?) + Start Container
    payload = [
        { "GlobalTriggers": [] },
        {
            "$type": "NINA.Sequencer.Container.SequenceContainer, NINA.Sequencer",
            "Name": "Start_Container",
            "Items": [],
            "Triggers": [],
            "Conditions": []
        }
    ]
    print("Test 2: Global Triggers + Start Container")
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        print(f"Status: {response.status_code}, Body: {response.text}\n")
    except Exception as e:
        print(f"Error: {e}\n")

if __name__ == "__main__":
    test_incremental()
