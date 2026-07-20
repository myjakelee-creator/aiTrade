from __future__ import annotations

import threading
from types import SimpleNamespace

import realtime_v2.worker_approved_minute_rollover_guard as guard
import realtime_v2.worker_momentum_1m_patch as momentum


class CanonicalState:
    next_date = "20260717"

    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "metric_session_state_date": "20260716",
            "approved_large_checkpoint_restored_count": 0,
        }
        self.quotes = {"000660": {"stock_code": "000660", "day_open": 100_000}}
        self.daily_values_by_code = {}
        self._approved_execution_stage = {"000660": {"execution_strength": 100}}
        self._approved_orderbook_stage = {"000660": {"bid_ask_ratio": 1.2}}
        self._approved_strength_stage = {"000660": {"strength_5m": 90}}
        self._approved_large_live = {"000660": {"buy_count": 2, "quality": "EXACT_LIVE"}}
        self._approved_large_seen = {"000660": {"a"}}
        self._approved_trade_value_last = {"000660": 100.0}
        self._approved_trade_value_buckets = {"000660": {1: 10.0}}
        self._approved_trade_value_partial = {"000660"}
        self._approved_last_publish_minute = 1
        self.daily_dirty = False

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def request_background_rebuild(self, *, reason, force=False):
        return None

    def ensure_metric_session_state_date(self, *args, **kwargs):
        self.status["metric_session_state_date"] = self.next_date
        return self.next_date

    def stage_approved_trade_events(self, events):
        return None

    def rows(self, limit=300):
        return [dict(value) for value in self.quotes.values()][:limit]


class CanonicalBase:
    State = CanonicalState
    DAILY_PERSIST_KEYS = ()


def _session(phase: str, date: str):
    return SimpleNamespace(
        phase=phase,
        trading_date=date,
        calendar_date=date,
        windows={"regular_start": "09:00"},
    )


def test_next_premarket_rollover_clears_momentum_and_approved_minute_state(monkeypatch):
    monkeypatch.setattr(guard, "market_session_now", lambda now=None: _session("premarket", "20260717"))
    monkeypatch.setattr(momentum, "_session", lambda now=None: _session("premarket", "20260717"))
    monkeypatch.setattr(momentum, "_expected_date", lambda now=None: "20260717")
    monkeypatch.setattr(momentum, "_system_minute_key", lambda now=None: 20260717 * 1440 + 8 * 60)

    class LocalState(CanonicalState):
        pass

    class LocalBase:
        State = LocalState
        DAILY_PERSIST_KEYS = ()

    guard.install(LocalBase)
    state = LocalState()
    state._approved_pipeline_date = "20260716"
    state._momentum_current["000660"] = {
        "minute_key": 20260716 * 1440 + 19 * 60 + 59,
        "trading_date": "20260716",
        "open": 100_000,
        "high": 101_000,
        "low": 99_000,
        "close": 100_500,
    }
    state._momentum_last_completed["000660"] = dict(state._momentum_current["000660"])
    state._momentum_vwap_scale["000660"] = 1_000.0
    for target in (state.quotes["000660"], state.daily_values_by_code.setdefault("000660", {})):
        target.update(
            {
                "momentum_vwap_signal": "가중돌파",
                "momentum_open_signal": "시가돌파",
                "momentum_trading_date": "20260716",
                "momentum_last_completed_candle": dict(state._momentum_current["000660"]),
            }
        )

    state.rows()

    assert state._approved_pipeline_date == "20260717"
    assert state._approved_execution_stage == {}
    assert state._approved_orderbook_stage == {}
    assert state._approved_strength_stage == {}
    assert state._approved_large_live == {}
    assert state._approved_trade_value_buckets == {}
    assert state._momentum_current == {}
    assert state._momentum_last_completed == {}
    assert state._momentum_vwap_scale == {}
    assert "momentum_vwap_signal" not in state.quotes["000660"]
    assert "momentum_open_signal" not in state.daily_values_by_code["000660"]
    assert state.status["momentum_1m_rollover_date"] == "20260717"
