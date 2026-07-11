from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timedelta
from threading import RLock
from types import SimpleNamespace

from realtime_v2 import strength5m_scheduler as scheduler_module
from realtime_v2.strength5m_definitive_preopen_patch import install


install()


class DummyBase:
    @staticmethod
    def normalize_code(value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:6] if len(digits) >= 6 else ""


class DummyProvider:
    def __init__(self, *, ready=False, registered=False):
        self._lock = RLock()
        self._running = True
        self._login_state = "connected" if ready else "requested"
        self._tr_event_connected = True
        self._control = object()
        self._qt_pump_running = True
        self._qt_pump_last_at = datetime.now().isoformat(timespec="seconds")
        self._realreg_succeeded = registered
        self._registered_codes = {"000001"} if registered else set()
        self._strength_probe_pending = deque()
        self._strength_probe_pending_codes = set()
        self._strength_probe_inflight = None
        self._strength_probe_timeout_sec = 10.0
        self._strength_probe_last_request_at = 0.0
        self._orderbook_probe_pending = deque()
        self._orderbook_probe_inflight = None
        self._orderbook_probe_last_request_at = 0.0
        self._opt10055_probe_pending = deque()
        self._opt10055_probe_inflight = None
        self._opt10055_probe_last_request_at = 0.0
        self._close_metrics_queue = deque()
        self._close_metrics_last_request_at = 0.0
        self.enqueue_calls = []
        self.errors = []

    def _strength_probe_ready_state_locked(self):
        if self._login_state != "connected":
            return False, f"login_state={self._login_state}"
        return True, "ready"

    def enqueue_strength_probe(self, code, priority="active", force=False, trading_date=None):
        self.enqueue_calls.append((code, priority, force, trading_date))
        item = {
            "stock_code": code,
            "priority": priority,
            "requested_at": datetime.now().isoformat(timespec="seconds"),
            "trading_date": trading_date,
            "next_retry_at_monotonic": time.monotonic(),
        }
        self._strength_probe_pending.append(item)
        self._strength_probe_pending_codes.add(code)
        return {"status": "pending"}

    def _strength_probe_error(self, code, message, requested_at=None, trading_date=None):
        self.errors.append((code, message, requested_at, trading_date))


WEEKEND = SimpleNamespace(
    phase="weekend",
    is_trading_day=False,
    accept_realtime=False,
    windows={"regular_start": "09:00"},
)


def _scheduler(provider):
    scheduler = scheduler_module.Strength5mScheduler(DummyBase, provider)
    scheduler.selected = ""
    scheduler._next_premarket = lambda _now=None: datetime.now() + timedelta(days=2)
    return scheduler


def test_provider_not_ready_never_enqueues_or_leaves_pending():
    provider = DummyProvider(ready=False, registered=False)
    scheduler = _scheduler(provider)
    plan = [
        {
            "stock_code": "000001",
            "lane": "top20",
            "row": {"stock_code": "000001", "strength_5m": None},
        }
    ]

    scheduler._preopen_cycle(WEEKEND, plan)

    assert provider.enqueue_calls == []
    assert len(provider._strength_probe_pending) == 0
    assert scheduler.preopen_mode == "preopen_wait_provider"
    assert scheduler.preopen_block_reason == "provider_not_ready:login_state=requested"


def test_ready_provider_enqueues_exactly_one_candidate():
    provider = DummyProvider(ready=True, registered=True)
    scheduler = _scheduler(provider)
    plan = [
        {
            "stock_code": "000001",
            "lane": "top20",
            "row": {"stock_code": "000001", "strength_5m": None},
        },
        {
            "stock_code": "000002",
            "lane": "top300",
            "row": {"stock_code": "000002", "strength_5m": None},
        },
    ]

    scheduler._preopen_cycle(WEEKEND, plan)

    assert len(provider.enqueue_calls) == 1
    assert provider.enqueue_calls[0][0] == "000001"
    assert scheduler.enqueue_count == 1
    assert scheduler.last_code == "000001"
    assert len(provider._strength_probe_pending) == 1


def test_unowned_orphan_pending_is_removed_instead_of_blocking_forever():
    provider = DummyProvider(ready=True, registered=True)
    provider._strength_probe_pending.append(
        {
            "stock_code": "009999",
            "requested_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    provider._strength_probe_pending_codes.add("009999")
    scheduler = _scheduler(provider)

    assert scheduler._idle(WEEKEND, gap_override=1.25) is False
    assert len(provider._strength_probe_pending) == 0
    assert "009999" not in provider._strength_probe_pending_codes
    assert scheduler.preopen_orphan_purge_count == 1
    assert scheduler.preopen_block_reason == "released_orphan"

    assert scheduler._idle(WEEKEND, gap_override=1.25) is True
    assert scheduler.preopen_block_reason == "ready"


def test_persisted_requested_marker_without_live_request_is_due():
    provider = DummyProvider(ready=True, registered=True)
    scheduler = _scheduler(provider)
    item = {
        "stock_code": "000001",
        "lane": "top300",
        "row": {
            "strength_5m": None,
            "strength_status": "requested",
        },
    }

    due, score = scheduler._preopen_candidate_due(
        item,
        final_window=False,
        now_mono=100.0,
    )

    assert due is True
    assert score == float("inf")


def test_close_metrics_timestamp_does_not_block_preopen_strength():
    provider = DummyProvider(ready=True, registered=True)
    provider._close_metrics_last_request_at = time.monotonic()
    scheduler = _scheduler(provider)

    assert scheduler._idle(WEEKEND, gap_override=1.25) is True
    assert scheduler.preopen_block_reason == "ready"
