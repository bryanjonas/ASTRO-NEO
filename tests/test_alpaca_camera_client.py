"""Throwaway protocol-correctness test for AlpacaCameraClient -- no real
hardware, verifies ImageBytes parsing against a synthetic, byte-exact
response matching the real format confirmed against actual hardware."""

import struct
import sys

sys.path.insert(0, "/app")

import httpx
import numpy as np

from app.services.alpaca_camera_client import AlpacaCameraClient, AlpacaError

failures = []


def check(label, condition):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


# --- Test 1: ImageBytes parsing against a synthetic, known-shape response ---
# dim1 = NumX (width) = 4, dim2 = NumY (height) = 3. Alpaca's ImageArray is
# [x, y] ordered -- lay out the flat buffer x-major (all y for x=0, then all
# y for x=1, ...) with value = x*10 + y, exactly matching how a real driver
# producing [x,y]-ordered data would serialize it.
dim1, dim2 = 4, 3
pixel_values = [x * 10 + y for x in range(dim1) for y in range(dim2)]
pixel_bytes = struct.pack(f"<{len(pixel_values)}H", *pixel_values)  # uint16

header = struct.pack(
    "<11i",
    1,  # MetadataVersion
    0,  # ErrorNumber
    1,  # ClientTransactionID
    2,  # ServerTransactionID
    44,  # DataStart
    2,  # ImageElementType (Int32, logical -- irrelevant, TransmissionElementType wins)
    8,  # TransmissionElementType (UInt16)
    2,  # Rank
    dim1,
    dim2,
    0,
)
synthetic_response = header + pixel_bytes

_orig_get = httpx.get
_orig_put = httpx.put


def _fake_request(url):
    return httpx.Request("GET", url)


def mock_get(url, params=None, timeout=None, headers=None):
    req = _fake_request(url)
    if headers and headers.get("Accept") == "application/imagebytes":
        return httpx.Response(200, content=synthetic_response, request=req)
    # imageready / other GET properties
    member = url.rsplit("/", 1)[-1]
    value = {"imageready": True, "name": "TEST-CAM"}.get(member, None)
    return httpx.Response(200, json={"Value": value, "ErrorNumber": 0, "ErrorMessage": ""}, request=req)


def mock_put(url, data=None, timeout=None):
    return httpx.Response(200, json={"ErrorNumber": 0, "ErrorMessage": ""}, request=_fake_request(url))


httpx.get = mock_get
httpx.put = mock_put

client = AlpacaCameraClient(base_url="http://fake-alpaca:11111", device_number=0)
arr = client.get_image_array()

check("shape is (height, width) = (3, 4)", arr.shape == (dim2, dim1))
check("dtype is uint16", arr.dtype == np.uint16)

all_correct = True
for x in range(dim1):
    for y in range(dim2):
        if arr[y, x] != x * 10 + y:
            all_correct = False
check("transpose puts value at [y, x] matching Alpaca's [x, y] ordering", all_correct)

check("is_image_ready() returns True", client.is_image_ready() is True)

# --- Test 2: error envelope handling on a bad ImageBytes header ---
bad_header = struct.pack(
    "<11i", 1, 1025, 1, 2, 44, 2, 8, 2, dim1, dim2, 0  # ErrorNumber=1025
)


def mock_get_error(url, params=None, timeout=None, headers=None):
    return httpx.Response(200, content=bad_header + pixel_bytes, request=_fake_request(url))


httpx.get = mock_get_error
try:
    client.get_image_array()
    check("AlpacaError raised on ImageBytes ErrorNumber != 0", False)
except AlpacaError as exc:
    check(f"AlpacaError raised on ImageBytes ErrorNumber != 0 ({exc})", True)

httpx.get = _orig_get
httpx.put = _orig_put

print()
if failures:
    print(f"{len(failures)} FAILURE(S):", failures)
    sys.exit(1)
else:
    print("All checks passed.")
