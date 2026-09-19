"""Throwaway protocol-correctness test for AlpacaMountClient -- no real
hardware, verifies request shape/parsing against a mock Alpaca server."""

import sys
sys.path.insert(0, "/app")

import httpx
from app.services.alpaca_mount_client import AlpacaMountClient, AlpacaError

# In-memory fake mount state
state = {
    "connected": True,
    "rightascension": 12.0,  # hours (=180 deg)
    "declination": 45.0,
    "slewing": False,
    "atpark": False,
    "tracking": False,
    "equatorialsystem": 1,  # Topocentric/JNOW
    "cansettracking": True,
}

captured_requests = []


def handler(request: httpx.Request) -> httpx.Response:
    captured_requests.append(request)
    member = request.url.path.rsplit("/", 1)[-1]
    if request.method == "GET":
        value = state.get(member)
        return httpx.Response(200, json={"Value": value, "ErrorNumber": 0, "ErrorMessage": ""})
    else:
        params = dict(httpx.QueryParams(request.content.decode()))
        if member == "slewtocoordinatesasync":
            state["rightascension"] = float(params["RightAscension"])
            state["declination"] = float(params["Declination"])
            state["slewing"] = True
        elif member == "synctocoordinates":
            pass  # accept, no state change needed for this test
        elif member == "tracking":
            state["tracking"] = params["Tracking"] == "True"
        elif member == "trackingrate":
            pass
        elif member == "connected":
            state["connected"] = params["Connected"] == "True"
        elif member in ("park", "unpark"):
            state["atpark"] = member == "park"
        return httpx.Response(200, json={"ErrorNumber": 0, "ErrorMessage": ""})


transport = httpx.MockTransport(handler)

# Monkeypatch httpx.get/put used inside AlpacaMountClient to route through
# the mock transport (simplest way to test without changing the client to
# accept an injected client object).
_orig_get = httpx.get
_orig_put = httpx.put


def mock_get(url, params=None, timeout=None):
    with httpx.Client(transport=transport) as client:
        return client.get(url, params=params)


def mock_put(url, data=None, timeout=None):
    with httpx.Client(transport=transport) as client:
        return client.put(url, data=data)


httpx.get = mock_get
httpx.put = mock_put

client = AlpacaMountClient(base_url="http://fake-alpaca:11111", device_number=0)

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


# Test 1: mount_info() converts hours->degrees and normalizes shape
info = client.mount_info()
check("mount_info ra_deg == 180.0 (12h * 15)", info["ra_deg"] == 180.0)
check("mount_info dec_deg == 45.0", info["dec_deg"] == 45.0)
check("mount_info is_connected True", info["is_connected"] is True)
check("mount_info is_tracking False initially", info["is_tracking"] is False)

# Test 2: slew() sends correct hours conversion
client.slew(ra_deg=90.0, dec_deg=30.0)
slew_req = [r for r in captured_requests if r.url.path.endswith("slewtocoordinatesasync")][0]
slew_params = dict(httpx.QueryParams(slew_req.content.decode()))
check("slew sends RightAscension=6.0 (90/15)", float(slew_params["RightAscension"]) == 6.0)
check("slew sends Declination=30.0", float(slew_params["Declination"]) == 30.0)

# Test 3: ensure_sidereal_tracking actually enables and verifies tracking
client.ensure_sidereal_tracking()
check("tracking now True after ensure_sidereal_tracking", state["tracking"] is True)

# Test 4: error envelope handling
def error_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ErrorNumber": 1025, "ErrorMessage": "Not connected"})


error_transport = httpx.MockTransport(error_handler)


def mock_get_error(url, params=None, timeout=None):
    with httpx.Client(transport=error_transport) as c:
        return c.get(url, params=params)


httpx.get = mock_get_error
error_client = AlpacaMountClient(base_url="http://fake-alpaca:11111")
try:
    error_client.mount_info_raw()
    check("AlpacaError raised on ErrorNumber != 0", False)
except AlpacaError as exc:
    check(f"AlpacaError raised on ErrorNumber != 0 ({exc})", True)

httpx.get = _orig_get
httpx.put = _orig_put

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("All checks passed.")
