"""Direct ASCOM Alpaca camera control -- Phase 2 of the direct_hardware branch.

Talks to the ZWO ASI585MC Pro's ASCOM driver via the same Alpaca bridge
(ASCOM Remote Server) as AlpacaMountClient, replacing NINA's fire-and-forget
exposure model: this app now owns the full exposure lifecycle (trigger,
poll ImageReady, pull pixel data, write the FITS file itself) instead of
polling the filesystem for NINA to produce a file.

Key protocol details, all confirmed against the real camera (not just the
spec) before writing this:

- The camera's raw ImageArray is RAW BAYER MOSAIC data (RGGB, offset 0,0),
  not debayered -- confirmed by comparing against a real NINA-captured
  FITS from the same session (BAYERPAT=RGGB header, visible 2x2 CFA
  modulation in the raw pixel values). This client preserves that: no
  debayering happens here, matching what the rest of the pipeline (solver,
  association) already expects from NINA-captured frames.

- Alpaca's `imagearray` endpoint, requested with `Accept: application/
  imagebytes`, returns a fast binary format instead of JSON (essential for
  a ~16.6MB 3840x2160 16-bit frame). Header layout confirmed by direct
  byte-level inspection of a real response, decoding to exactly the
  documented ASCOM ImageBytes v1 metadata format:
    offset  0: MetadataVersion (int32) = 1
    offset  4: ErrorNumber (int32)
    offset  8: ClientTransactionID (int32)
    offset 12: ServerTransactionID (int32)
    offset 16: DataStart (int32) -- byte offset where pixel data begins
    offset 20: ImageElementType (int32) -- logical element type enum
    offset 24: TransmissionElementType (int32) -- actual wire type enum
    offset 28: Rank (int32) -- 2 for mono/raw-Bayer single-plane
    offset 32: Dimension1 (int32) -- NumX (width)
    offset 36: Dimension2 (int32) -- NumY (height)
    offset 40: Dimension3 (int32) -- 0 when Rank=2
  Confirmed live: DataStart=44, TransmissionElementType=8 (UInt16), Rank=2,
  Dimension1=3840, Dimension2=2160, and total response size exactly
  matched 44 + 3840*2160*2 bytes.

- Alpaca's ImageArray is documented as [x, y] ordered (X varies fastest
  within the logical array), which is transposed relative to the
  conventional FITS/numpy image layout of (height, width) with Y as the
  first axis -- this matches the reference Alpaca Python client (alpyca)
  transposing for the same reason. Reshape to (Dimension1, Dimension2) in
  C order, then transpose, to get a (height, width) array matching what
  NINA's own saved FITS files use.
"""

from __future__ import annotations

import itertools
import logging
import struct
import time
from typing import Any

import httpx
import numpy as np

logger = logging.getLogger(__name__)

# ASCOM ImageArrayElementTypes enum (subset actually seen in practice).
_ELEMENT_TYPE_DTYPES = {
    1: np.int16,
    2: np.int32,
    3: np.float64,
    4: np.float32,
    5: np.uint64,
    6: np.uint8,
    7: np.int64,
    8: np.uint16,
    9: np.uint32,
}

_IMAGEBYTES_HEADER_SIZE = 44
_IMAGEBYTES_HEADER_STRUCT = struct.Struct("<11i")  # 11 little-endian int32s


class AlpacaError(Exception):
    """An Alpaca device returned ErrorNumber != 0, or a response couldn't be parsed."""


class AlpacaCameraClient:
    """Owns the full exposure lifecycle against an Alpaca-exposed ASCOM
    camera driver: trigger, poll, pixel retrieval. Does not write FITS
    files itself -- see fits_writer.py for that, kept separate so this
    class stays a thin, testable protocol client."""

    def __init__(
        self,
        base_url: str,
        device_number: int = 0,
        client_id: int = 1,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.device_number = device_number
        self.client_id = client_id
        self.timeout = timeout
        self._transaction_id = itertools.count(1)

    def _url(self, member: str) -> str:
        return f"{self.base_url}/api/v1/camera/{self.device_number}/{member}"

    def _request(
        self, method: str, member: str, params: dict[str, Any] | None = None, raw: bool = False
    ) -> Any:
        params = dict(params or {})
        params["ClientID"] = self.client_id
        params["ClientTransactionID"] = next(self._transaction_id)
        # See AlpacaMountClient for why this normalization is needed --
        # httpx encodes Python bool as lowercase "true"/"false"; Alpaca's
        # spec (.NET heritage) requires capitalized "True"/"False".
        params = {
            key: ("True" if value is True else "False" if value is False else value)
            for key, value in params.items()
        }

        url = self._url(member)
        headers = {"Accept": "application/imagebytes"} if raw else None
        try:
            if method == "GET":
                response = httpx.get(url, params=params, timeout=self.timeout, headers=headers)
            else:
                response = httpx.put(url, data=params, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AlpacaError(f"Failed to reach Alpaca device at {url}: {exc}") from exc

        if raw:
            return response.content

        data = response.json()
        error_number = data.get("ErrorNumber", 0)
        if error_number:
            raise AlpacaError(
                f"Alpaca error {error_number} on {member}: {data.get('ErrorMessage')}"
            )
        return data.get("Value")

    def _get(self, member: str) -> Any:
        return self._request("GET", member)

    def _put(self, member: str, params: dict[str, Any] | None = None) -> None:
        self._request("PUT", member, params)

    # --- Connection ---

    def connect_camera(self, connect: bool = True) -> str:
        self._put("connected", {"Connected": connect})
        return "connected" if connect else "disconnected"

    # --- Capabilities (queried once, cached by the caller if desired) ---

    def get_capabilities(self) -> dict[str, Any]:
        """Static-ish camera properties useful for FITS header construction."""
        return {
            "name": self._get("name"),
            "sensor_type": self._get("sensortype"),
            "bayer_offset_x": self._get("bayeroffsetx"),
            "bayer_offset_y": self._get("bayeroffsety"),
            "pixel_size_x": self._get("pixelsizex"),
            "pixel_size_y": self._get("pixelsizey"),
            "max_adu": self._get("maxadu"),
            "camera_x_size": self._get("cameraxsize"),
            "camera_y_size": self._get("cameraysize"),
            "can_set_ccd_temperature": self._get("cansetccdtemperature"),
            "electrons_per_adu": self._get("electronsperadu"),
        }

    # --- Exposure setup ---

    def set_binning(self, bin_x: int, bin_y: int) -> None:
        self._put("binx", {"BinX": bin_x})
        self._put("biny", {"BinY": bin_y})

    def set_gain(self, gain: int) -> None:
        self._put("gain", {"Gain": gain})

    def get_gain(self) -> int:
        return int(self._get("gain"))

    def get_ccd_temperature(self) -> float | None:
        try:
            return float(self._get("ccdtemperature"))
        except Exception:
            return None

    # --- Exposure lifecycle ---

    def start_exposure(self, exposure_seconds: float, light: bool = True) -> None:
        self._put("startexposure", {"Duration": exposure_seconds, "Light": light})

    def abort_exposure(self) -> None:
        self._put("abortexposure")

    def is_image_ready(self) -> bool:
        return bool(self._get("imageready"))

    def wait_for_image_ready(self, timeout: float, poll_interval: float = 0.5) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self.is_image_ready():
                    return
            except Exception as exc:
                logger.warning("Image-ready poll failed, will retry: %s", exc)
            time.sleep(poll_interval)
        raise AlpacaError(f"Image not ready after {timeout}s")

    def get_image_array(self) -> np.ndarray:
        """Fetch the raw pixel array via the binary ImageBytes transfer,
        returned as a (height, width) array in the camera's native element
        type (UInt16 for the ASI585MC Pro) -- no debayering applied."""
        raw = self._request("GET", "imagearray", raw=True)
        if len(raw) < _IMAGEBYTES_HEADER_SIZE:
            raise AlpacaError(f"ImageBytes response too short: {len(raw)} bytes")

        (
            metadata_version,
            error_number,
            _client_txn,
            _server_txn,
            data_start,
            _element_type,
            transmission_type,
            rank,
            dim1,
            dim2,
            dim3,
        ) = _IMAGEBYTES_HEADER_STRUCT.unpack_from(raw, 0)

        if metadata_version != 1:
            raise AlpacaError(f"Unsupported ImageBytes MetadataVersion: {metadata_version}")
        if error_number:
            raise AlpacaError(f"ImageBytes response carries ErrorNumber={error_number}")

        dtype = _ELEMENT_TYPE_DTYPES.get(transmission_type)
        if dtype is None:
            raise AlpacaError(f"Unsupported TransmissionElementType: {transmission_type}")

        pixel_bytes = raw[data_start:]
        expected_pixels = dim1 * dim2 * (dim3 if rank == 3 and dim3 else 1)
        arr = np.frombuffer(pixel_bytes, dtype=dtype, count=expected_pixels)

        if rank == 2:
            # Alpaca's ImageArray is [x, y] ordered -- transpose to the
            # conventional (height, width) layout. Confirmed against a
            # real response: reshape (dim1, dim2) then .T.
            arr = arr.reshape((dim1, dim2)).T
        elif rank == 3:
            arr = arr.reshape((dim1, dim2, dim3)).transpose(1, 0, 2)
        else:
            raise AlpacaError(f"Unsupported ImageBytes Rank: {rank}")

        return np.ascontiguousarray(arr)


__all__ = ["AlpacaCameraClient", "AlpacaError"]
