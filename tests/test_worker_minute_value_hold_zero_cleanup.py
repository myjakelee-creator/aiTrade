from __future__ import annotations

import threading

import realtime_v2.worker_minute_value_hold_patch as minute_hold


def _minute_values(current: float, previous: float = 0.0, ratio: float = 0.0):
    return {
        "trade_value_1m_eok": current,
        "trade_value_prev_1m_eok": previous,
        "trade_value_1m_ratio_pct": ratio,
        "trade_value_1m_quality": "COMPLETE_MINUTE",
        "trade_value_1m_observed_at": "2026-07-17T06:00:00",
        "trade_value_1m_source_trading_date": "20260716",
        "trade_value_1m_source": "qax_fid14_minute_delta",
    }


def test_zero_snapshot_is_treated_as_absent():
    snapshot = minute_hold._snapshot(_minute_values(0.0))

    assert all(present is False for present, _value in snapshot.values())
    assert minute_hold.PATCH_VERSION == "minute_value_hold_v2"


def test_closed_session_clears_preexisting_zero_from_row_and_daily_state(monkeypatch):
    monkeypatch.setattr(minute_hold, "_phase_name", lambda: "closed")
    monkeypatch.setattr(minute_hold, "_completed_minute", lambda: 100)

    class FakeState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.daily_dirty = False
            values = {"stock_code": "000001", **_minute_values(0.0)}
            self.quotes = {"000001": dict(values)}
            self.daily_values_by_code = {"000001": dict(values)}
            self._approved_trade_value_buckets = {}

        def _mark_daily_dirty(self):
            self.daily_dirty = True

        def rows(self, limit=300):
            return [dict(self.quotes["000001"])]

    class FakeBase:
        State = FakeState

    minute_hold.install(FakeBase)
    state = FakeState()
    row = state.rows()[0]

    for key in minute_hold.MINUTE_VALUE_KEYS:
        assert key not in row
        assert key not in state.quotes["000001"]
        assert key not in state.daily_values_by_code["000001"]
    assert state.daily_dirty is True
    assert state.status["minute_value_hold_version"] == "minute_value_hold_v2"
    assert state.status["minute_value_invalid_zero_cleared_fields"] > 0


def test_positive_daily_last_good_survives_when_quote_contains_zero(monkeypatch):
    monkeypatch.setattr(minute_hold, "_phase_name", lambda: "before_market")
    monkeypatch.setattr(minute_hold, "_completed_minute", lambda: 100)

    class FakeState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.daily_dirty = False
            self.quotes = {"000001": {"stock_code": "000001", **_minute_values(0.0)}}
            self.daily_values_by_code = {
                "000001": {"stock_code": "000001", **_minute_values(12.3, 8.5, 144.7)}
            }
            self._approved_trade_value_buckets = {}

        def _mark_daily_dirty(self):
            self.daily_dirty = True

        def rows(self, limit=300):
            row = dict(self.quotes["000001"])
            row.update(self.daily_values_by_code["000001"])
            return [row]

    class FakeBase:
        State = FakeState

    minute_hold.install(FakeBase)
    state = FakeState()
    row = state.rows()[0]

    assert "trade_value_1m_eok" not in state.quotes["000001"]
    assert state.daily_values_by_code["000001"]["trade_value_1m_eok"] == 12.3
    assert row["trade_value_1m_eok"] == 12.3
    assert row["trade_value_1m_ratio_pct"] == 144.7


def test_positive_completed_bucket_is_not_protected_from_new_publish():
    assert minute_hold.minute_value_should_hold("regular", {100: 12.3}, 100) is False
