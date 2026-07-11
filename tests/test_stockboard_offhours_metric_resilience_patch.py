from __future__ import annotations

import time
from collections import deque
from threading import RLock
from types import SimpleNamespace

from realtime_v2.offhours_metric_completion_patch import OffhoursMetricCompletionDrain
from realtime_v2 import strength5m_definitive_preopen_patch as definitive
from realtime_v2.offhours_metric_resilience_patch import install


definitive.OffhoursStrengthDrain = OffhoursMetricCompletionDrain
install()


class DummyBase:
    @staticmethod
    def normalize_code(value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:6] if len(digits) >= 6 else ""


class DummyControl:
    def __init__(self):
        self.calls = []

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        return 0


class DummyStore:
    def update_close_metrics(self, _code, _values):
        return None


class DummyProvider:
    _STRENGTH_PROBE_RQNAME = "stockboard_opt10046_probe"
    _STRENGTH_PROBE_TRCODE = "opt10046"
    _STRENGTH_PROBE_SCREEN = "9020"
    _ORDERBOOK_PROBE_RQNAME = "stockboard_opt10004_probe"
    _ORDERBOOK_PROBE_TRCODE = "opt10004"
    _ORDERBOOK_PROBE_SCREEN = "9021"

    def __init__(self):
        self._lock = RLock()
        self._running = True
        self._login_state = "connected"
        self._tr_event_connected = True
        self._control = DummyControl()
        self._qt_pump_running = True
        self._realreg_succeeded = True
        self._registered_codes = {"000001"}
        self._strength_probe_pending = deque()
        self._strength_probe_pending_codes = set()
        self._orderbook_probe_pending = deque()
        self._orderbook_probe_pending_codes = set()
        self._opt10055_probe_pending = deque()
        self._strength_probe_inflight = None
        self._orderbook_probe_inflight = None
        self._opt10055_probe_inflight = None
        self._strength_probe_cache = {}
        self._orderbook_probe_cache = {}
        self._strength_probe_last_result = None
        self._orderbook_probe_last_result = None
        self._strength_probe_last_request_at = 0.0
        self._orderbook_probe_last_request_at = 0.0
        self._strength_probe_last_by_code = {}
        self._orderbook_probe_last_by_code = {}
        self.store = DummyStore()

    def _strength_probe_ready_state_locked(self):
        return True, "ready"

    def _strength_probe_error(self, *_args, **_kwargs):
        return None

    def _orderbook_probe_error(self, *_args, **_kwargs):
        return None


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
                {
                    "stock_code": "000001",
                    "strength_5m": None,
                    "execution_strength": None,
                    "bid_ask_ratio": 1.2,
                }
            ]
        }

    @staticmethod
    def build_lane_plan(_base, payload, _selected):
        return [
            {"stock_code": row["stock_code"], "lane": "top20", "row": row}
            for row in payload["rows"]
        ]


def _drain():
    return OffhoursMetricCompletionDrain(DummyBase, DummyProvider(), DummySchedulerModule)


def test_stale_rate_gap_is_released_and_next_request_starts():
    drain = _drain()
    drain.mode = "rate_gap"
    drain.queue.append(("strength", "000001"))
    drain.next_request_at = time.monotonic() + 100.0
    drain.rate_gap_started_monotonic = time.monotonic() - 10.0

    drain.tick()

    assert drain.rate_gap_recovery_count == 1
    assert drain.request_count == 1
    assert drain.current == ("strength", "000001")
    assert drain.provider._strength_probe_inflight["stock_code"] == "000001"


def test_tick_exception_is_caught_and_next_tick_can_continue():
    drain = _drain()
    del drain.provider._strength_probe_last_by_code

    drain.tick()

    assert drain.tick_error_count == 1
    assert drain.mode == "tick_error_recovered"
    assert drain.current is None
    assert "AttributeError" in drain.tick_last_error

    drain.provider._strength_probe_last_by_code = {}
    drain.next_request_at = 0.0
    drain.tick()

    assert drain.request_count == 1
    assert drain.current == ("strength", "000001")
    assert drain.stats()["last_tick_age_sec"] is not None
