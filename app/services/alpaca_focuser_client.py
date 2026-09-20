"""Direct ASCOM Alpaca focuser control -- Phase 4 (autofocus) of the
direct_hardware branch.

Confirmed live against the real focuser (a ZWO EAF) before writing this:
absolute positioning supported, MaxStep=12000, current position 3679
(matches tonight's real NINA-captured FITS header FOCPOS=3679 exactly --
same physical device). No temperature compensation available on this
unit (TempCompAvailable=False), so this client doesn't bother with it.
"""

from __future__ import annotations

import itertools
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AlpacaError(Exception):
    """An Alpaca device returned ErrorNumber != 0."""


class AlpacaFocuserClient:
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
        return f"{self.base_url}/api/v1/focuser/{self.device_number}/{member}"

    def _request(self, method: str, member: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        params["ClientID"] = self.client_id
        params["ClientTransactionID"] = next(self._transaction_id)
        params = {
            key: ("True" if value is True else "False" if value is False else value)
            for key, value in params.items()
        }
        url = self._url(member)
        try:
            if method == "GET":
                response = httpx.get(url, params=params, timeout=self.timeout)
            else:
                response = httpx.put(url, data=params, timeout=self.timeout)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AlpacaError(f"Failed to reach Alpaca device at {url}: {exc}") from exc

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

    def connect(self, connect: bool = True) -> None:
        self._put("connected", {"Connected": connect})

    def get_position(self) -> int:
        return int(self._get("position"))

    def is_moving(self) -> bool:
        return bool(self._get("ismoving"))

    def get_max_step(self) -> int:
        return int(self._get("maxstep"))

    def move(self, position: int) -> None:
        self._put("move", {"Position": position})

    def move_and_wait(self, position: int, timeout: float = 60.0, poll_interval: float = 0.25) -> None:
        self.move(position)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_moving():
                return
            time.sleep(poll_interval)
        raise AlpacaError(f"Focuser still moving after {timeout}s")


__all__ = ["AlpacaFocuserClient", "AlpacaError"]
