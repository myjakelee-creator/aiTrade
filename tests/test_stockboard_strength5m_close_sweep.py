from __future__ import annotations

from datetime import datetime
from threading import RLock
from types import SimpleNamespace

import realtime_v2.market_session as market_session
import realtime_v2.strength5m_scheduler as strength5m_scheduler
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
        self.calls = []
        self.response_status = "requested"

    def enqueue_strength_probe(self, code, priority, force, trading_date=None):
        self.calls.append((code, priority, force, trading_date))
        return {"status": self.response_status}


SESSION = SimpleNamespace(
    phase="aftermarket",
    is_trading_day=True,
    accept_realtime=True,
    windows={
        "premarket_start": "08:00",
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


def test_postclose_held_value_retries_but_is_capped():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {
        "stock_code": "000001",
        "lane": "top300",
        "row": {
            "strength_5m": 123.4,
            "strength_status": "held_last_valid",
            "strength_snapshot_at": "2026-07-10T15:31:00",
        },
    }

    due, _overdue = scheduler._close_sweep_due(
        item,
        SESSION,
        datetime(2026, 7, 10, 15, 45),
    )
    assert due is True

    scheduler.close_sweep_attempts["000001"] = scheduler.close_sweep_max_attempts
    due, _overdue = scheduler._close_sweep_due(
        item,
        SESSION,
        datetime(2026, 7, 10, 15, 45),
    )
    assert due is False


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


def test_next_premarket_skips_weekend(monkeypatch):
    monkeypatch.setattr(market_session, "_load_config", lambda: {})
    assert market_session.next_premarket_datetime(
        datetime(2026, 7, 10, 20, 1)
    ) == datetime(2026, 7, 13, 8, 0)


def test_next_premarket_skips_holiday_and_uses_delayed_open(monkeypatch):
    monkeypatch.setattr(
        market_session,
        "_load_config",
        lambda: {
            "special_days": {
                "20260713": {"closed": True, "reason": "holiday"},
                "20260714": {"open_delay_minutes": 60, "reason": "delayed"},
            }
        },
    )
    assert market_session.next_premarket_datetime(
        datetime(2026, 7, 10, 20, 1)
    ) == datetime(2026, 7, 14, 9, 0)


def test_last_completed_trading_date_uses_current_day_only_after_close(monkeypatch):
    monkeypatch.setattr(market_session, "_load_config", lambda: {})
    assert market_session.last_completed_trading_date(
        datetime(2026, 7, 10, 15, 29)
    ) == "20260709"
    assert market_session.last_completed_trading_date(
        datetime(2026, 7, 10, 15, 31)
    ) == "20260710"


def test_preopen_window_covers_close_weekend_holiday_and_before_market():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    for phase in ("after_wait", "aftermarket", "closed", "weekend", "holiday", "before_market"):
        assert scheduler._preopen_window(SimpleNamespace(phase=phase)) is True
    for phase in ("premarket", "opening_call", "regular", "closing_call"):
        assert scheduler._preopen_window(SimpleNamespace(phase=phase)) is False


def test_preopen_missing_scan_uses_displayed_strength_5m_only():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    plan = [
        {"stock_code": "000001", "lane": "top20", "row": {"strength_5m": 120.0}},
        {"stock_code": "000002", "lane": "top20", "row": {"strength_5m": 0}},
        {"stock_code": "000003", "lane": "top300", "row": {}},
        {
            "stock_code": "000004",
            "lane": "top300",
            "row": {"strength_5m": None, "last_valid_strength_5m": 99.0},
        },
    ]
    missing = scheduler._preopen_missing_items(plan)
    assert [item["stock_code"] for item in missing] == ["000002", "000003", "000004"]


def test_preopen_complete_stops_all_queries(monkeypatch):
    provider = DummyProvider()
    scheduler = Strength5mScheduler(DummyBase, provider)
    scheduler.selected = ""
    scheduler.payload = {
        "rows": [
            {"stock_code": "000001", "strength_5m": 100.0},
            {"stock_code": "000002", "strength_5m": 110.0},
        ]
    }
    session = SimpleNamespace(
        phase="weekend",
        is_trading_day=False,
        accept_realtime=False,
        windows={"regular_start": "09:00"},
    )
    monkeypatch.setattr(scheduler, "_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_session", lambda: session)
    monkeypatch.setattr(
        scheduler,
        "_next_premarket",
        lambda now=None: datetime(2026, 7, 13, 8, 0),
    )

    scheduler.cycle()

    assert provider.calls == []
    assert scheduler.preopen_mode == "preopen_fill_complete"
    assert scheduler.preopen_missing_count == 0


def test_preopen_queries_only_missing_rows_in_lane_priority(monkeypatch):
    provider = DummyProvider()
    scheduler = Strength5mScheduler(DummyBase, provider)
    scheduler.selected = ""
    scheduler.payload = {
        "rows": [
            {"stock_code": "000001", "strength_5m": 100.0},
            {"stock_code": "000002", "strength_5m": None},
            {"stock_code": "000003", "strength_5m": None},
        ]
    }
    session = SimpleNamespace(
        phase="weekend",
        is_trading_day=False,
        accept_realtime=False,
        windows={"regular_start": "09:00"},
    )
    monkeypatch.setattr(scheduler, "_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_session", lambda: session)
    monkeypatch.setattr(scheduler, "_idle", lambda session=None, gap_override=None: True)
    monkeypatch.setattr(
        scheduler,
        "_next_premarket",
        lambda now=None: datetime.now().replace(hour=23, minute=59, second=0, microsecond=0),
    )
    monkeypatch.setattr(
        strength5m_scheduler,
        "last_completed_trading_date",
        lambda now=None: "20260710",
    )

    scheduler.cycle()

    assert provider.calls[0][0] == "000002"
    assert provider.calls[0][3] == "20260710"
    assert scheduler.preopen_missing_count == 2
    assert scheduler.preopen_mode == "preopen_fill_active"


def test_preopen_retry_backoff_and_final_window_override():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {"stock_code": "000001", "lane": "top300", "row": {}}
    scheduler.preopen_attempts["000001"] = 1
    scheduler.preopen_retry_at["000001"] = 1000.0

    due, _ = scheduler._preopen_candidate_due(
        item,
        final_window=False,
        now_mono=900.0,
    )
    assert due is False

    due, _ = scheduler._preopen_candidate_due(
        item,
        final_window=True,
        now_mono=900.0,
    )
    assert due is True


def test_global_backoff_stops_preopen_requests(monkeypatch):
    provider = DummyProvider()
    scheduler = Strength5mScheduler(DummyBase, provider)
    scheduler.payload = {"rows": [{"stock_code": "000001", "strength_5m": None}]}
    scheduler.preopen_global_backoff_until = 10**12
    scheduler.preopen_global_backoff_until_iso = "2099-01-01T00:00:00"
    session = SimpleNamespace(
        phase="holiday",
        is_trading_day=False,
        accept_realtime=False,
        windows={"regular_start": "09:00"},
    )
    monkeypatch.setattr(scheduler, "_refresh", lambda: None)
    monkeypatch.setattr(scheduler, "_session", lambda: session)
    monkeypatch.setattr(
        scheduler,
        "_next_premarket",
        lambda now=None: datetime(2099, 1, 2, 8, 0),
    )

    scheduler.cycle()

    assert provider.calls == []
    assert scheduler.preopen_mode == "preopen_fill_backoff"
