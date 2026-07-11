from __future__ import annotations

import threading
from threading import Event
from types import SimpleNamespace

from realtime_v2.qt_main_thread_openapi_patch import (
    install_collector_main,
    install_provider,
)


class FakeApp:
    def __init__(self):
        self.calls = 0

    def processEvents(self):
        self.calls += 1


def _inline_provider():
    install_provider()
    from kiwoom_data_provider import KiwoomOpenApiRealtimeProvider

    provider = KiwoomOpenApiRealtimeProvider()
    provider._stockboard_inline_qt_mode = True
    provider._stockboard_inline_qt_owner_ident = threading.get_ident()
    provider._stockboard_inline_qt_owner_name = threading.current_thread().name
    provider._app = FakeApp()
    provider._qt_pump_stop_event = Event()
    provider._running = True
    provider._qt_pump_running = True
    return provider


def test_inline_qt_pump_processes_openapi_queues_on_owner_thread():
    provider = _inline_provider()
    calls = []
    provider._process_pending_realtime_requests = lambda: calls.append("realtime")
    provider._process_orderbook_rotation = lambda: calls.append("rotation")
    provider._process_strength_probe_queue = lambda: calls.append("strength")
    provider._process_orderbook_probe_queue = lambda: calls.append("orderbook")
    provider._process_opt10055_probe_queue = lambda: calls.append("opt10055")
    provider._process_close_metrics_queue = lambda: calls.append("close_metrics")

    assert provider.pump_inline_qt_once() is True
    assert provider._app.calls == 1
    assert calls == [
        "realtime",
        "rotation",
        "strength",
        "orderbook",
        "opt10055",
        "close_metrics",
    ]
    assert provider._qt_pump_running is True
    assert provider._qt_pump_last_at


def test_inline_qt_pump_rejects_non_owner_thread():
    provider = _inline_provider()
    provider._stockboard_inline_qt_owner_ident = threading.get_ident() + 1

    assert provider.pump_inline_qt_once() is False
    assert provider._qt_pump_running is False
    assert "non-owner thread" in provider._last_error


def test_collector_main_patch_replaces_entrypoint_once():
    original = lambda: 7
    base = SimpleNamespace(main=original)

    install_collector_main(base)
    first = base.main
    install_collector_main(base)

    assert first is not original
    assert base.main is first
    assert base._stockboard_qt_main_thread_main_installed is True
