from __future__ import annotations

import time
from collections import deque
from threading import RLock
from types import SimpleNamespace

from realtime_v2.offhours_metric_completion_patch import OffhoursMetricCompletionDrain


class DummyBase:
    @staticmethod
    def normalize_code(value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:6] if len(digits) >= 6 else ""


class DummyStore:
    def __init__(self):
        self.events = []

    def update_close_metrics(self, code, values):
        self.events.append((code, dict(values)))
        return values


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
        self._registered_codes = {"000001", "000002", "000003"}
        self._strength_probe_pending = deque()
        self._strength_probe_pending_codes = set()
        self._strength_probe_inflight = None
        self._strength_probe_cache = {}
        self._strength_probe_last_result = None
        self._strength_probe_last_request_at = 0.0
        self._strength_probe_last_by_code = {}
        self._orderbook_probe_pending = deque()
        self._orderbook_probe_pending_codes = set()
        self._orderbook_probe_inflight = None
        self._orderbook_probe_cache = {}
        self._orderbook_probe_last_result = None
        self._orderbook_probe_last_request_at = 0.0
        self._orderbook_probe_last_by_code = {}
        self._opt10055_probe_pending = deque()
        self._opt10055_probe_inflight = None
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

    def _orderbook_probe_error(
        self,
        code,
        message,
        requested_at=None,
        status="error",
    ):
        result = {
            "stock_code": code,
            "orderbook_status": status,
            "orderbook_error": message,
            "orderbook_requested_at": requested_at,
        }
        self._orderbook_probe_cache[code] = result
        self._orderbook_probe_last_result = result
        return result


class DummySchedulerModule:
    rows = []
    phase = "weekend"

    @classmethod
    def market_session_now(cls):
        return SimpleNamespace(phase=cls.phase)

    @staticmethod
    def last_completed_trading_date(_now):
        return "20260710"

    @classmethod
    def _read_json_url(cls, _url):
        return {"rows": [dict(row) for row in cls.rows]}

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


def _drain(rows):
    DummySchedulerModule.rows = [dict(row) for row in rows]
    DummySchedulerModule.phase = "weekend"
    return OffhoursMetricCompletionDrain(
        DummyBase,
        DummyProvider(),
        DummySchedulerModule,
    )


def test_refresh_finds_all_three_metric_types():
    drain = _drain(
        [
            {"stock_code": "000001"},
            {
                "stock_code": "000002",
                "strength_5m": 120.0,
                "bid_ask_ratio": 1.2,
            },
            {
                "stock_code": "000003",
                "strength_5m": 110.0,
                "execution_strength": 95.0,
            },
        ]
    )

    drain._refresh(100.0)

    assert drain.missing_strength5_count == 1
    assert drain.missing_execution_count == 2
    assert drain.missing_orderbook_count == 2
    assert ("strength", "000001") in drain.queue
    assert ("strength", "000002") in drain.queue
    assert ("orderbook", "000001") in drain.queue
    assert ("orderbook", "000003") in drain.queue


def test_strength_result_fills_5m_and_execution_together():
    drain = _drain([{"stock_code": "000001"}])
    provider = drain.provider
    task = ("strength", "000001")
    drain.current = task
    drain.current_needs_strength5 = True
    drain.current_needs_execution = True
    drain.attempts[task] = 1
    provider._strength_probe_cache["000001"] = {
        "stock_code": "000001",
        "strength_status": "ok",
        "strength_5m": 123.4,
        "realtime_strength_snapshot": 111.2,
        "strength_snapshot_at": "2026-07-11T12:00:00",
    }

    drain._finish()

    assert drain.success_count == 1
    assert drain.strength_success_count == 1
    mapped = [
        values
        for code, values in provider.store.events
        if code == "000001" and "execution_strength" in values
    ]
    assert mapped
    assert mapped[-1]["execution_strength"] == 111.2
    assert mapped[-1]["last_valid_execution_strength"] == 111.2


def test_failed_task_has_no_max_attempt_stop_and_retries_with_long_backoff():
    drain = _drain([{"stock_code": "000001"}])
    task = ("strength", "000001")
    drain.current = task
    drain.current_needs_strength5 = True
    drain.current_needs_execution = True
    drain.attempts[task] = 4
    drain.provider._strength_probe_cache["000001"] = {
        "stock_code": "000001",
        "strength_status": "no_data",
    }
    before = time.monotonic()

    drain._finish()

    assert drain.attempts[task] == 4
    assert drain.retry_at[task] >= before + 7190
    drain.retry_at[task] = 0.0
    drain.last_refresh_at = 0.0
    drain._refresh(100.0)
    assert task in drain.queue


def test_orderbook_request_uses_direct_inflight_without_pending_queue():
    drain = _drain(
        [
            {
                "stock_code": "000001",
                "strength_5m": 120.0,
                "execution_strength": 100.0,
            }
        ]
    )

    drain.tick()

    assert drain.current == ("orderbook", "000001")
    assert drain.provider._orderbook_probe_pending == deque()
    assert drain.provider._orderbook_probe_inflight["stock_code"] == "000001"
    assert drain.provider._orderbook_probe_inflight["owner"] == "offhours_metric_completion"
    assert drain.orderbook_request_count == 1


def test_actual_premarket_disables_offhours_completion():
    drain = _drain([{"stock_code": "000001"}])
    DummySchedulerModule.phase = "premarket"

    drain.tick()

    assert drain.mode == "inactive_session"
    assert drain.current is None
    assert drain.request_count == 0
