from __future__ import annotations

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace

import realtime_v2.worker_orderbook_live_display_guard as guard


def _base_for(row):
    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}

        def rows(self, limit=300):
            return [dict(row)][:limit]

    class Base:
        pass

    Base.State = State
    return Base


def _session(phase: str):
    return SimpleNamespace(phase=phase)


def test_active_session_hides_rest_snapshot(monkeypatch):
    monkeypatch.setattr(guard, "market_session_now", lambda: _session("regular"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    base = _base_for(
        {
            "stock_code": "000660",
            "bid_ask_ratio": 3.56,
            "orderbook_source": "ka10004_rest_lowload",
            "orderbook_received_at": now,
        }
    )
    guard.install(base)
    state = base.State()
    result = state.rows()[0]

    assert "bid_ask_ratio" not in result
    assert result["orderbook_status"] == "nonrealtime_orderbook_hidden"
    assert result["orderbook_available"] is False
    assert state.status["orderbook_live_display_hidden_nonlive_count"] == 1


def test_active_session_keeps_fresh_realtime_orderbook(monkeypatch):
    monkeypatch.setattr(guard, "market_session_now", lambda: _session("regular"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    base = _base_for(
        {
            "stock_code": "000660",
            "bid_ask_ratio": 1.23,
            "orderbook_source": "kiwoom_rest_ws_orderbook",
            "orderbook_received_at": now,
        }
    )
    guard.install(base)
    state = base.State()
    result = state.rows()[0]

    assert result["bid_ask_ratio"] == 1.23
    assert result["orderbook_status"] == "live_realtime_ok"
    assert result["orderbook_available"] is True
    assert state.status["orderbook_live_display_visible_count"] == 1


def test_active_session_hides_stale_realtime_orderbook(monkeypatch):
    monkeypatch.setattr(guard, "market_session_now", lambda: _session("regular"))
    old = (datetime.now().astimezone() - timedelta(seconds=10)).isoformat(
        timespec="seconds"
    )
    base = _base_for(
        {
            "stock_code": "000660",
            "bid_ask_ratio": 1.23,
            "orderbook_source": "kiwoom_rest_ws_orderbook",
            "orderbook_received_at": old,
        }
    )
    guard.install(base)
    state = base.State()
    result = state.rows()[0]

    assert "bid_ask_ratio" not in result
    assert result["orderbook_status"] == "stale_realtime_orderbook_hidden"
    assert state.status["orderbook_live_display_hidden_stale_count"] == 1


def test_closed_session_preserves_close_hold(monkeypatch):
    monkeypatch.setattr(guard, "market_session_now", lambda: _session("closed"))
    base = _base_for(
        {
            "stock_code": "000660",
            "bid_ask_ratio": 1.56,
            "orderbook_source": "ka10004_rest_lowload",
            "orderbook_status": "previous_session_final_hold",
        }
    )
    guard.install(base)
    state = base.State()
    result = state.rows()[0]

    assert result["bid_ask_ratio"] == 1.56
    assert result["orderbook_status"] == "previous_session_final_hold"
    assert state.status["orderbook_live_display_guard_active"] is False
