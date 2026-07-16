from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import realtime_v2.worker_five_metric_display_policy as policy
import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle


def _session(phase: str):
    return SimpleNamespace(
        phase=phase,
        trading_date="20260716",
        calendar_date="20260716",
    )


def _now(offset_sec: int = 0) -> str:
    return (datetime.now().astimezone() + timedelta(seconds=offset_sec)).isoformat(
        timespec="seconds"
    )


def _base_for(row):
    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.quotes = {"000660": deepcopy(row)}
            self.daily_values_by_code = {"000660": deepcopy(row)}
            self.daily_dirty = False

        def rows(self, limit=300):
            return [deepcopy(self.quotes["000660"])][:limit]

        def _mark_daily_dirty(self):
            self.daily_dirty = True

        def _apply_orderbook(self, event):
            values = event.get("values") or {}
            quote = self.quotes.setdefault("000660", {"stock_code": "000660"})
            quote["bid_ask_ratio"] = values.get("bid_ask_ratio")
            quote["orderbook_received_at"] = event.get("ts")

    class Base:
        State = State
        DAILY_PERSIST_KEYS = ()

        @staticmethod
        def merged_event_values(event):
            return dict(event.get("values") or {})

    return Base


def _valid_active_row():
    now = _now()
    return {
        "stock_code": "000660",
        "rank": 1,
        "bid_ask_ratio": 1.56,
        "orderbook_source": "qax_realtime_orderbook",
        "orderbook_live": True,
        "orderbook_received_at": now,
        "orderbook_source_trading_date": "20260716",
        "execution_strength": 98.7,
        "execution_strength_source": "kiwoom_rest_ws_0B_fid228",
        "execution_strength_received_at": now,
        "execution_source_trading_date": "20260716",
        "strength_5m": 94.6,
        "strength_source": "ka10046_rest_lowload",
        "strength_snapshot_at": now,
        "strength_source_trading_date": "20260716",
        "program_net": 3824,
        "program_net_source": "ka90004_tr_singleflight",
        "program_net_updated_at": now,
        "program_source_trading_date": "20260716",
        "large_trade_net_count": 2,
        "large_trade_source": "ka10055_rest_incremental",
        "large_trade_updated_at": now,
        "large_trade_source_trading_date": "20260716",
    }


def test_active_session_accepts_only_current_fresh_approved_sources(monkeypatch):
    monkeypatch.setattr(policy, "market_session_now", lambda now=None: _session("regular"))
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260716")
    base = _base_for(_valid_active_row())
    policy.install(base)
    state = base.State()
    row = state.rows()[0]

    assert row["bid_ask_ratio"] == 1.56
    assert row["execution_strength"] == 98.7
    assert row["strength_5m"] == 94.6
    assert row["program_net"] == 3824
    assert row["large_trade_net_count"] == 2
    assert state.status["five_metric_orderbook_visible_count"] == 1
    assert state.status["five_metric_execution_visible_count"] == 1
    assert state.status["five_metric_strength5_visible_count"] == 1
    assert state.status["five_metric_program_visible_count"] == 1
    assert state.status["five_metric_large_trade_visible_count"] == 1


def test_active_session_hides_rest_orderbook_stale_and_untrusted_values(monkeypatch):
    monkeypatch.setattr(policy, "market_session_now", lambda now=None: _session("regular"))
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260716")
    row = _valid_active_row()
    row.update(
        {
            "orderbook_source": "ka10004_rest_lowload",
            "execution_strength_received_at": _now(-30),
            "strength_snapshot_at": _now(-700),
            "large_trade_net_count": 0,
            "large_trade_source": "price_only_collector_no_fid15",
        }
    )
    base = _base_for(row)
    policy.install(base)
    state = base.State()
    result = state.rows()[0]

    assert "bid_ask_ratio" not in result
    assert "execution_strength" not in result
    assert "strength_5m" not in result
    assert result["program_net"] == 3824
    assert "large_trade_net_count" not in result
    assert result["orderbook_status"] == "orderbook_source_untrusted_hidden"
    assert result["execution_strength_status"] == "execution_stale_hidden"
    assert result["strength_status"] == "strength5_stale_hidden"
    assert result["large_trade_status"] == "large_trade_source_untrusted_hidden"


def test_hold_phase_preserves_verified_previous_session_values(monkeypatch):
    monkeypatch.setattr(policy, "market_session_now", lambda now=None: _session("closed"))
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260716")
    old = _now(-3600)
    row = _valid_active_row()
    row.update(
        {
            "orderbook_source": "ka10004_rest_lowload",
            "orderbook_received_at": old,
            "execution_strength_source": "kiwoom_rest_ws_0B_fid228_close_hold",
            "execution_strength_received_at": old,
            "strength_source": "opt10046",
            "strength_snapshot_at": old,
            "program_net_updated_at": old,
            "large_trade_updated_at": old,
        }
    )
    base = _base_for(row)
    policy.install(base)
    state = base.State()
    result = state.rows()[0]

    assert result["bid_ask_ratio"] == 1.56
    assert result["execution_strength"] == 98.7
    assert result["strength_5m"] == 94.6
    assert result["program_net"] == 3824
    assert result["large_trade_net_count"] == 2
    assert result["orderbook_display_basis"] == "previous_session_final_until_premarket"
    assert state.status["five_metric_display_policy_mode"] == "hold"


def test_realtime_orderbook_event_is_stamped_for_unified_policy(monkeypatch):
    monkeypatch.setattr(policy, "market_session_now", lambda now=None: _session("regular"))
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260716")
    base = _base_for({"stock_code": "000660"})
    policy.install(base)
    state = base.State()
    ts = _now()
    state._apply_orderbook(
        {
            "stock_code": "000660",
            "ts": ts,
            "values": {"bid_ask_ratio": 1.23},
        }
    )

    quote = state.quotes["000660"]
    daily = state.daily_values_by_code["000660"]
    assert quote["orderbook_source"] == "qax_realtime_orderbook"
    assert quote["orderbook_source_trading_date"] == "20260716"
    assert quote["orderbook_live"] is True
    assert daily["orderbook_source"] == "qax_realtime_orderbook"
    assert state.daily_dirty is True
