from __future__ import annotations

from datetime import datetime
from threading import RLock
from types import SimpleNamespace

from realtime_v2.strength5m_scheduler import Strength5mScheduler


class DummyBase:
    @staticmethod
    def normalize_code(value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:6] if len(digits) >= 6 else ""


class DummyProvider:
    def __init__(self):
        self._lock = RLock()
        self._strength_probe_inflight = None
        self._orderbook_probe_inflight = None
        self._opt10055_probe_inflight = None
        self._strength_probe_pending = []
        self._orderbook_probe_pending = []
        self._opt10055_probe_pending = []
        self._close_metrics_queue = []
        self._strength_probe_last_request_at = 0.0
        self._orderbook_probe_last_request_at = 0.0
        self._opt10055_probe_last_request_at = 0.0
        self._close_metrics_last_request_at = 0.0


SESSION = SimpleNamespace(
    phase="aftermarket",
    is_trading_day=True,
    accept_realtime=True,
    windows={
        "regular_close": "15:30",
        "regular_start": "09:00",
        "aftermarket_end": "20:00",
    },
)


def test_regular_close_sweep_is_active_during_after_wait_and_early_aftermarket():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())

    after_wait = SimpleNamespace(**{**SESSION.__dict__, "phase": "after_wait"})
    assert scheduler._close_sweep_cutoff(
        after_wait,
        datetime(2026, 7, 10, 15, 35),
    ) == datetime(2026, 7, 10, 15, 30)
    assert scheduler._close_sweep_cutoff(
        SESSION,
        datetime(2026, 7, 10, 15, 45),
    ) == datetime(2026, 7, 10, 15, 30)


def test_regular_close_sweep_remains_available_through_aftermarket():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())

    assert scheduler._close_sweep_cutoff(
        SESSION,
        datetime(2026, 7, 10, 17, 0),
    ) == datetime(2026, 7, 10, 15, 30)
    assert scheduler._close_sweep_cutoff(
        SESSION,
        datetime(2026, 7, 10, 20, 0),
    ) is None


def test_row_with_only_preclose_strength_is_queried_again_after_close():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {
        "stock_code": "000001",
        "lane": "top300",
        "row": {
            "strength_5m": 123.4,
            "strength_status": "ok",
            "strength_snapshot_at": "2026-07-10T15:20:00",
        },
    }

    due, overdue = scheduler._close_sweep_due(
        item,
        SESSION,
        datetime(2026, 7, 10, 15, 45),
    )
    assert due is True
    assert overdue == 600


def test_row_with_postclose_strength_is_not_queried_twice_by_sweep():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {
        "stock_code": "000001",
        "lane": "top300",
        "row": {
            "strength_5m": 123.4,
            "strength_status": "ok",
            "strength_snapshot_at": "2026-07-10T15:31:00",
        },
    }

    due, _overdue = scheduler._close_sweep_due(
        item,
        SESSION,
        datetime(2026, 7, 10, 15, 45),
    )
    assert due is False


def test_pending_query_is_not_duplicated_during_close_sweep():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {
        "stock_code": "000001",
        "lane": "top20",
        "row": {
            "strength_status": "requested",
            "strength_snapshot_at": "2026-07-10T15:20:00",
        },
    }

    due, _overdue = scheduler._close_sweep_due(
        item,
        SESSION,
        datetime(2026, 7, 10, 15, 45),
    )
    assert due is False
