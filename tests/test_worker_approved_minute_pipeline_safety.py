from __future__ import annotations

import threading

import realtime_v2.worker_approved_minute_pipeline_safety as safety


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.daily_values_by_code = {
            "000660": {
                "approved_large_checkpoint_buy_count": 3,
                "approved_large_checkpoint_sell_count": 1,
                "approved_large_checkpoint_buy_sum_eok": 2.5,
                "approved_large_checkpoint_sell_sum_eok": 0.7,
                "approved_large_checkpoint_quality": "EXACT_LIVE",
                "approved_large_checkpoint_observed_at": "2026-07-16T09:10:05+09:00",
                "approved_large_checkpoint_source_date": "20260716",
            }
        }
        self._approved_large_live = {}
        self.daily_dirty = False

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def stage_approved_trade_events(self, events):
        return None

    def mark_approved_stream_disconnect(self):
        return None


class FakeBase:
    State = FakeState
    DAILY_PERSIST_KEYS = ()


def test_safety_restores_checkpoint_as_gap_possible(monkeypatch):
    original_status = safety.__import__(
        "realtime_v2.worker_realtime_strength_ws_patch",
        fromlist=["RealtimeStrengthWebSocket"],
    ).RealtimeStrengthWebSocket._status
    try:
        safety.install(FakeBase)
        state = FakeState()
        live = state._approved_large_live["000660"]
        assert live["buy_count"] == 3
        assert live["sell_count"] == 1
        assert live["quality"] == "GAP_POSSIBLE"
        assert state.status["approved_large_checkpoint_restored_count"] == 1
        assert "approved_large_checkpoint_buy_count" in FakeBase.DAILY_PERSIST_KEYS
    finally:
        safety.__import__(
            "realtime_v2.worker_realtime_strength_ws_patch",
            fromlist=["RealtimeStrengthWebSocket"],
        ).RealtimeStrengthWebSocket._status = original_status


def test_safety_source_has_fail_closed_orderbook_and_no_rest_fallback():
    source = safety.__file__
    text = open(source, encoding="utf-8").read()
    assert '"disabled_no_events"' in text
    assert '"dash_no_rest_fallback"' in text
    assert "connection.close()" in text
    assert "CHECKPOINT_INTERVAL_SEC = 5.0" in text
