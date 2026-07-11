from __future__ import annotations

from threading import RLock
from types import SimpleNamespace

import pytest

import realtime_v2.openapi_native_handle_patch as native_patch


class FakeApp:
    def __init__(self):
        self.process_count = 0

    def processEvents(self):
        self.process_count += 1


class FakeControl:
    def __init__(self, hwnd: int):
        self.hwnd = hwnd
        self.calls = []

    def setAttribute(self, *_args):
        self.calls.append("setAttribute")

    def resize(self, width, height):
        self.calls.append(("resize", width, height))

    def move(self, x, y):
        self.calls.append(("move", x, y))

    def show(self):
        self.calls.append("show")

    def winId(self):
        self.calls.append("winId")
        return self.hwnd


def _provider(hwnd: int):
    return SimpleNamespace(
        _lock=RLock(),
        _control=FakeControl(hwnd),
        _app=FakeApp(),
    )


def test_native_handle_is_created_before_openapi_login(monkeypatch):
    monkeypatch.setattr(native_patch.os, "name", "posix")
    provider = _provider(12345)

    hwnd = native_patch._ensure_openapi_native_handle(provider)

    assert hwnd == 12345
    assert provider._stockboard_openapi_native_handle_ready is True
    assert provider._stockboard_openapi_native_hwnd == 12345
    assert provider._stockboard_openapi_native_handle_error is None
    assert ("resize", 2, 2) in provider._control.calls
    assert ("move", -32000, -32000) in provider._control.calls
    assert "show" in provider._control.calls
    assert "winId" in provider._control.calls
    assert provider._app.process_count >= 2


def test_invalid_native_handle_blocks_login(monkeypatch):
    monkeypatch.setattr(native_patch.os, "name", "posix")
    provider = _provider(0)

    with pytest.raises(RuntimeError, match="native HWND is invalid"):
        native_patch._ensure_openapi_native_handle(provider)
