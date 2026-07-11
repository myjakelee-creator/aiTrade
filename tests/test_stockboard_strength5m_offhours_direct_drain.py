from __future__ import annotations

import time
from collections import deque
from threading import RLock
from types import SimpleNamespace

from realtime_v2.strength5m_definitive_preopen_patch import OffhoursStrengthDrain


class DummyBase:
    @staticmethod
    def normalize_code(value):
        text = "".join(ch for ch in str(value or "") if ch.isdigit())
        return text[:6] if len(text) >= 6 else ""


class DummyStore:
    def __init__(self):
        self.events = []

    def update_close_metrics(self, code, values):
        self.events.append((code, dict(values)))


class DummyControl:
    def __init__(self):
        self.calls = []

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        return 0


class DummyProvider:
    _STRENGTH_PROBE_RQNAME = "stockboard_opt10046_probe"
    _STRENGTH_PROBE_TRCODE = "opt10046"
    _STRENGTH_PROBE_SCREEN = "9020"

    def __init__(self):
        self._lock = RLock()
        self._running = True
        self._login_state = "connected"
        self._tr_event_connected = True
        self._control = DummyControl()
        self._qt_pump_running = True
        self._realreg_succeeded = True
        self._registered_codes = {"000001", "000002"}
        self._strength_probe_pending = deque()
        self._strength_probe_pending_codes = set()
        self._strength_probe_inflight = None
        self._strength_probe_cache = {}
        self._strength_probe_last_result = None
        self._strength_probe_last_request_at = 0.0
        self._strength_probe_last_by_code = {}
        self.store = DummyStore()

    def _strength_probe_ready_state_locked(self):
        return True, "ready"

    def _strength_probe_error(
        self,
        code,
        message,
        requested_at=None,
        trading_date=None,
    ):
        result = {
            "stock_code": code,
            "strength_status": "error",
            "strength_error": message,
            "strength_requested_at": requested_at,
            "trading_date": trading_date,
        }
        self._strength_probe_cache[code] = result
        self._strength_probe_last_result = result
        return result


class DummySchedulerModule:
    @staticmethod
    def market_session_now():
        return SimpleNamespace(phase="weekend")

    @staticmethod
    def last_completed_trading_date(_now):
        return "20260710"

    @staticmethod
    def _read_json_url(_url):
        return {
            "rows": [
                {"stock_code": "000001", "strength_5m": None},
                {"stock_code": "000002", "strength_5m": None},
            ]
        }

    @staticmethod
    def build_lane_plan(_base, payload, _selected):
        return [
            {
                "stock_code": row["stock_code"],
                "lane": "top20",
                "row": row,
            }
            for row in payload["rows"]
        ]


def test_offhours_drain_purges_pending_and_uses_direct_inflight():
    provider = DummyProvider()
    provider._strength_probe_pending.append(
        {"stock_code": "999999", "requested_at": None}
    )
    provider._strength_probe_pending_codes.add("999999")
    drain = OffhoursStrengthDrain(DummyBase, provider, DummySchedulerModule)

    drain.tick()

    assert provider._strength_probe_pending == deque()
    assert provider._strength_probe_pending_codes == set()
    assert drain.pending_purge_count == 1
    assert drain.enqueue_count == 1
    assert drain.current == "000001"
    assert provider._strength_probe_inflight["stock_code"] == "000001"
    assert provider._strength_probe_inflight["owner"] == "offhours_direct_drain"


def test_offhours_drain_completes_one_and_moves_to_next_without_pending_queue():
    provider = DummyProvider()
    drain = OffhoursStrengthDrain(DummyBase, provider, DummySchedulerModule)

    drain.tick()
    first = drain.current
    assert first == "000001"

    provider._strength_probe_cache[first] = {
        "stock_code": first,
        "strength_status": "ok",
        "strength_5m": 123.4,
    }
    provider._strength_probe_inflight = None
    drain.current_started = time.monotonic() - 1.0
    drain.tick()

    assert drain.success_count == 1
    assert provider._strength_probe_pending == deque()
    assert drain.current is None

    drain.next_request_at = 0.0
    drain.tick()

    assert drain.enqueue_count == 2
    assert drain.current == "000002"
    assert provider._strength_probe_pending == deque()


def test_offhours_drain_timeout_skips_code_instead_of_blocking_queue():
    provider = DummyProvider()
    drain = OffhoursStrengthDrain(DummyBase, provider, DummySchedulerModule)
    drain.timeout_sec = 8.0

    drain.tick()
    first = drain.current
    assert first == "000001"
    drain.current_started = time.monotonic() - 20.0
    drain.tick()

    assert drain.current is None
    assert drain.timeout_count == 1
    assert drain.error_count == 1
    assert provider._strength_probe_pending == deque()

    drain.next_request_at = 0.0
    drain.tick()
    assert drain.current == "000002"
