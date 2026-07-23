from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

from realtime_v2 import after_close_live_hold_patch as patch
from realtime_v2 import worker_board_trading_date_guard as guard_module


class _State:
    def __init__(self, *, trade_count: int, received_at: str | None):
        self.lock = threading.RLock()
        self.status = {"trade_count": trade_count}
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "price": 273000,
                "trade_price": 273000,
                "change_rate": 4.8,
                "trade_value_eok": 77400.0,
                "received_at": received_at,
            }
        }


class _Guard:
    original_calls = 0

    def apply(self, state, now=None):
        type(self).original_calls += 1
        state.status["board_display_basis"] = "portable_exact_close"
        return True


def _install(monkeypatch):
    _Guard.original_calls = 0
    monkeypatch.setattr(guard_module, "PortableBoardGuard", _Guard)
    patch.install(SimpleNamespace(State=_State))


def test_closed_session_keeps_current_day_worker_rows_without_exact_fallback(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260723", "closed", False),
    )
    state = _State(trade_count=6002, received_at="2026-07-23T20:00:03+09:00")
    guard = _Guard()

    assert guard.apply(state, datetime(2026, 7, 23, 20, 0, 5)) is True
    assert _Guard.original_calls == 0
    assert state.status["board_display_basis"] == "in_memory_after_close_live_hold"
    assert state.status["after_close_live_hold_active"] is True
    assert state.status["after_close_live_hold_row_count"] == 1


def test_late_event_after_close_remains_eligible_and_updates_last_value(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260723", "closed", False),
    )
    state = _State(trade_count=1, received_at="2026-07-23T20:00:01+09:00")
    guard = _Guard()

    assert guard.apply(state) is True
    state.quotes["005930"].update(
        {
            "price": 273500,
            "trade_price": 273500,
            "trade_value_eok": 77500.0,
            "received_at": "2026-07-23T20:00:08+09:00",
        }
    )
    state.status["trade_count"] = 2
    assert guard.apply(state) is True
    assert state.quotes["005930"]["price"] == 273500
    assert state.quotes["005930"]["trade_value_eok"] == 77500.0
    assert _Guard.original_calls == 0


def test_restart_without_accepted_trades_uses_existing_exact_fallback(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260723", "closed", False),
    )
    state = _State(trade_count=0, received_at=None)
    guard = _Guard()

    assert guard.apply(state) is True
    assert _Guard.original_calls == 1
    assert state.status["board_display_basis"] == "portable_exact_close"
    assert state.status["after_close_live_hold_active"] is False
    assert state.status["after_close_live_hold_basis"] == "portable_exact_fallback"


def test_weekend_uses_calendar_completed_date_for_existing_friday_data(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260724", "weekend", False),
    )
    state = _State(trade_count=500, received_at="2026-07-24T20:00:05+09:00")
    guard = _Guard()

    assert guard.apply(state, datetime(2026, 7, 25, 10, 0, 0)) is True
    assert _Guard.original_calls == 0
    assert state.status["after_close_live_hold_target_date"] == "20260724"


def test_active_session_is_unchanged(monkeypatch):
    _install(monkeypatch)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260727", "premarket", True),
    )
    state = _State(trade_count=500, received_at="2026-07-24T20:00:05+09:00")
    guard = _Guard()

    assert guard.apply(state, datetime(2026, 7, 27, 8, 0, 0)) is True
    assert _Guard.original_calls == 1
    assert state.status["after_close_live_hold_basis"] == "active_session"
