from __future__ import annotations

import threading
from types import SimpleNamespace

import realtime_v2.worker_approved_minute_rollover_guard as guard


class FakeState:
    next_date = "20260716"

    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "metric_session_state_date": "20260715",
            "approved_large_checkpoint_restored_count": 0,
        }
        self._approved_execution_stage = {"000660": {"execution_strength": 100}}
        self._approved_orderbook_stage = {"000660": {"bid_ask_ratio": 1.2}}
        self._approved_strength_stage = {"000660": {"strength_5m": 90}}
        self._approved_large_live = {"000660": {"buy_count": 2, "quality": "EXACT_LIVE"}}
        self._approved_large_seen = {"000660": {"a"}}
        self._approved_trade_value_last = {"000660": 100.0}
        self._approved_trade_value_buckets = {"000660": {1: 10.0}}
        self._approved_trade_value_partial = {"000660"}
        self._approved_last_publish_minute = 1

    def ensure_metric_session_state_date(self, *args, **kwargs):
        self.status["metric_session_state_date"] = self.next_date
        return self.next_date

    def stage_approved_trade_events(self, events):
        return None

    def rows(self, limit=300):
        return [{"stock_code": "000660"}]


class FakeBase:
    State = FakeState


def _session(phase: str, date: str):
    return SimpleNamespace(phase=phase, trading_date=date, calendar_date=date)


def test_rollover_clears_intraday_state_at_next_premarket(monkeypatch):
    monkeypatch.setattr(guard, "market_session_now", lambda now=None: _session("premarket", "20260716"))
    guard.install(FakeBase)
    state = FakeState()
    state._approved_pipeline_date = "20260715"

    state.rows()

    assert state._approved_pipeline_date == "20260716"
    assert state._approved_execution_stage == {}
    assert state._approved_orderbook_stage == {}
    assert state._approved_strength_stage == {}
    assert state._approved_large_live == {}
    assert state._approved_trade_value_buckets == {}
    assert state._approved_large_full_session_coverage is True
    assert state.status["approved_pipeline_rollover_count"] == 1


def test_mid_session_start_marks_large_trade_quality_gap_possible(monkeypatch):
    class LocalState(FakeState):
        pass

    class LocalBase:
        State = LocalState

    monkeypatch.setattr(guard, "market_session_now", lambda now=None: _session("regular", "20260716"))
    guard.install(LocalBase)
    state = LocalState()

    assert state._approved_large_full_session_coverage is False
    assert state._approved_large_live["000660"]["quality"] == "GAP_POSSIBLE"
