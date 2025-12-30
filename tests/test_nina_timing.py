import time

import pytest

from app.services.nina_client import NinaBridgeService


def test_wait_for_mount_ready_respects_settle_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    nina = NinaBridgeService(base_url="http://example.invalid")
    statuses = [
        {"Slewing": True},
        {"Slewing": True},
        {"Slewing": False},
        {"Slewing": False},
    ]
    call_count = 0

    def fake_request(method: str, path: str, *args, **kwargs):
        nonlocal call_count
        assert method == "GET"
        assert path == "/equipment/mount/info"
        idx = min(call_count, len(statuses) - 1)
        call_count += 1
        return statuses[idx]

    monkeypatch.setattr(nina, "_request", fake_request)

    settle_seconds = 0.03
    start = time.monotonic()
    nina.wait_for_mount_ready(timeout=1.0, poll_interval=0.005, settle_seconds=settle_seconds)
    elapsed = time.monotonic() - start

    assert elapsed >= settle_seconds
    assert call_count >= 3


def test_wait_for_mount_ready_resets_when_slewing_reappears(monkeypatch: pytest.MonkeyPatch) -> None:
    nina = NinaBridgeService(base_url="http://example.invalid")
    statuses = [
        {"Slewing": True},
        {"Slewing": False},
        {"Slewing": True},
        {"Slewing": False},
        {"Slewing": False},
    ]
    call_count = 0

    def fake_request(method: str, path: str, *args, **kwargs):
        nonlocal call_count
        idx = min(call_count, len(statuses) - 1)
        call_count += 1
        return statuses[idx]

    monkeypatch.setattr(nina, "_request", fake_request)

    nina.wait_for_mount_ready(timeout=1.0, poll_interval=0.005, settle_seconds=0.02)
    assert call_count >= 4


def test_wait_for_mount_ready_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    nina = NinaBridgeService(base_url="http://example.invalid")

    def fake_request(method: str, path: str, *args, **kwargs):
        return {"Slewing": True}

    monkeypatch.setattr(nina, "_request", fake_request)

    with pytest.raises(Exception, match="Mount is still slewing"):
        nina.wait_for_mount_ready(timeout=0.05, poll_interval=0.005, settle_seconds=0.01)


def test_wait_for_camera_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    nina = NinaBridgeService(base_url="http://example.invalid")
    statuses = [
        {"IsExposing": True},
        {"IsExposing": True},
        {"IsExposing": False},
    ]
    call_count = 0

    def fake_request(method: str, path: str, *args, **kwargs):
        nonlocal call_count
        assert method == "GET"
        assert path == "/equipment/camera/info"
        idx = min(call_count, len(statuses) - 1)
        call_count += 1
        return statuses[idx]

    monkeypatch.setattr(nina, "_request", fake_request)

    nina.wait_for_camera_idle(timeout=1.0, poll_interval=0.005)
    assert call_count >= 2


def test_wait_for_camera_idle_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    nina = NinaBridgeService(base_url="http://example.invalid")

    def fake_request(method: str, path: str, *args, **kwargs):
        return {"IsExposing": True}

    monkeypatch.setattr(nina, "_request", fake_request)

    with pytest.raises(Exception, match="Camera never reached idle"):
        nina.wait_for_camera_idle(timeout=0.05, poll_interval=0.005)
