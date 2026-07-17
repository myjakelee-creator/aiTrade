from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

import realtime_v2.market_session as market_session
import realtime_v2.worker_approved_minute_rollover_guard as guard
import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle


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


def _session(phase: str, date: str, *, is_trading_day: bool = True):
    return SimpleNamespace(
        phase=phase,
        trading_date=date,
        calendar_date=date,
        is_trading_day=is_trading_day,
    )


def test_market_calendar_marks_20260717_as_holiday():
    current = datetime(2026, 7, 17, 8, 3)
    session = market_session.market_session_now(current)

    assert session.phase == "holiday"
    assert session.phase_label == "휴장일"
    assert session.is_trading_day is False
    assert session.accept_realtime is False
    assert market_session.last_completed_trading_date(current) == "20260716"
    assert market_session.next_premarket_datetime(current) == datetime(2026, 7, 20, 8, 0)


def test_rollover_clears_intraday_state_at_next_premarket(monkeypatch):
    monkeypatch.setattr(
        guard,
        "market_session_now",
        lambda now=None: _session("premarket", "20260716"),
    )
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

    monkeypatch.setattr(
        guard,
        "market_session_now",
        lambda now=None: _session("regular", "20260716"),
    )
    guard.install(LocalBase)
    state = LocalState()

    assert state._approved_large_full_session_coverage is False
    assert state._approved_large_live["000660"]["quality"] == "GAP_POSSIBLE"


def test_holiday_repeated_rows_hold_metrics_without_rollover_or_publish(monkeypatch):
    expires_at = "2026-07-17T08:00:00"

    def entry(group: str, values: dict):
        return {
            "stock_code": "000660",
            "group": group,
            "source_trading_date": "20260716",
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "expires_at": expires_at,
            "values": values,
        }

    class HolidayState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {
                "metric_session_state_date": "20260716",
                "approved_large_checkpoint_restored_count": 0,
            }
            self._approved_execution_stage = {"000660": {"execution_strength": 98.7}}
            self._approved_orderbook_stage = {"000660": {"bid_ask_ratio": 1.56}}
            self._approved_strength_stage = {"000660": {"strength_5m": 94.6}}
            self._approved_large_live = {
                "000660": {"buy_count": 2, "quality": "EXACT_LIVE"}
            }
            self._approved_large_seen = {"000660": {"a"}}
            self._approved_trade_value_last = {"000660": 100.0}
            self._approved_trade_value_buckets = {"000660": {1: 10.0}}
            self._approved_trade_value_partial = {"000660"}
            self._approved_last_publish_minute = 1
            self.publish_calls = 0
            self.six_metric_lifecycle_by_group = {
                "orderbook": {
                    "000660": entry(
                        "orderbook",
                        {
                            "bid_ask_ratio": 1.56,
                            "bid_pct": 61,
                            "ask_pct": 39,
                            "orderbook_source": "qax_realtime_orderbook",
                            "orderbook_status": "ok",
                            "orderbook_received_at": "2026-07-16T15:30:00",
                        },
                    )
                },
                "execution": {
                    "000660": entry(
                        "execution",
                        {
                            "execution_strength": 98.7,
                            "execution_strength_source": "kiwoom_rest_ws_0B_fid228",
                            "execution_strength_status": "ok",
                            "execution_strength_received_at": "2026-07-16T15:30:00",
                        },
                    )
                },
                "strength5": {
                    "000660": entry(
                        "strength5",
                        {
                            "strength_5m": 94.6,
                            "strength_source": "ka10046_rest_lowload",
                            "strength_status": "ok",
                            "strength_snapshot_at": "2026-07-16T15:30:00",
                        },
                    )
                },
            }

        def ensure_metric_session_state_date(self, *args, **kwargs):
            self.status["metric_session_state_date"] = "20260716"
            return "20260716"

        def stage_approved_trade_events(self, events):
            return None

        def publish_approved_minute_metrics(self, force=False):
            self.publish_calls += 1
            self._approved_trade_value_buckets.clear()
            return True

        def rows(self, limit=300):
            return [{"stock_code": "000660"}]

    class HolidayBase:
        State = HolidayState

    monkeypatch.setattr(
        guard,
        "market_session_now",
        lambda now=None: _session("holiday", "20260717", is_trading_day=False),
    )
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260716")
    monkeypatch.setattr(
        lifecycle,
        "_next_premarket_boundary",
        lambda now=None: datetime(2026, 7, 20, 8, 0),
    )
    guard.install(HolidayBase)
    state = HolidayState()

    first = state.rows()[0]
    assert state.status["approved_non_trading_hold_rebased_entry_count"] == 3
    second = state.rows()[0]
    published = state.publish_approved_minute_metrics(force=True)

    for row in (first, second):
        assert row["bid_ask_ratio"] == 1.56
        assert row["execution_strength"] == 98.7
        assert row["strength_5m"] == 94.6
        assert row["orderbook_display_basis"] == "previous_session_final_until_premarket"
    assert state._approved_execution_stage == {"000660": {"execution_strength": 98.7}}
    assert state._approved_orderbook_stage == {"000660": {"bid_ask_ratio": 1.56}}
    assert state._approved_strength_stage == {"000660": {"strength_5m": 94.6}}
    assert state._approved_trade_value_buckets == {"000660": {1: 10.0}}
    assert state._approved_large_live["000660"]["quality"] == "EXACT_LIVE"
    assert published is False
    assert state.publish_calls == 0
    assert state.status["approved_pipeline_non_trading_hold"] is True
    assert state.status["approved_non_trading_hold_restored_group_count"] == 3
    assert state.status["approved_non_trading_hold_rebased_entry_count"] == 0
    assert (
        state.six_metric_lifecycle_by_group["orderbook"]["000660"]["expires_at"]
        == "2026-07-20T08:00:00"
    )
    assert state.status["approved_minute_publish_suppressed_non_trading"] == 1


def test_holiday_restores_missing_strengths_from_exact_previous_daily_state(monkeypatch):
    expires_at = "2026-07-20T08:00:00"

    def orderbook_entry():
        return {
            "stock_code": "000660",
            "group": "orderbook",
            "source_trading_date": "20260716",
            "captured_at": "2026-07-16T15:30:00",
            "expires_at": expires_at,
            "values": {
                "bid_ask_ratio": 1.56,
                "orderbook_source": "qax_realtime_orderbook",
                "orderbook_status": "ok",
                "orderbook_received_at": "2026-07-16T15:30:00",
            },
        }

    class PreviousDailyState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {
                "metric_session_state_date": "20260716",
                "approved_large_checkpoint_restored_count": 0,
            }
            self._approved_execution_stage = {}
            self._approved_orderbook_stage = {}
            self._approved_strength_stage = {}
            self._approved_large_live = {}
            self._approved_large_seen = {}
            self._approved_trade_value_last = {}
            self._approved_trade_value_buckets = {}
            self._approved_trade_value_partial = set()
            self._approved_last_publish_minute = 1
            self.daily_values_by_code = {}
            self.quotes = {}
            self.six_metric_lifecycle_dirty = False
            self.six_metric_lifecycle_by_group = {
                "orderbook": {"000660": orderbook_entry()},
                "execution": {},
                "strength5": {},
            }

        def ensure_metric_session_state_date(self, *args, **kwargs):
            return "20260716"

        def rows(self, limit=300):
            return [{"stock_code": "000660"}]

    class PreviousDailyBase:
        State = PreviousDailyState

    monkeypatch.setattr(
        guard,
        "market_session_now",
        lambda now=None: _session("holiday", "20260717", is_trading_day=False),
    )
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260716")
    monkeypatch.setattr(
        lifecycle,
        "_next_premarket_boundary",
        lambda now=None: datetime(2026, 7, 20, 8, 0),
    )

    def read_exact(path):
        assert str(path).endswith("daily_state_20260716.json")
        return {
            "trading_date": "20260716",
            "codes": {
                "000660": {
                    "last_valid_execution_strength": 97.2,
                    "last_valid_strength_5m": 93.4,
                    "last_valid_strength_at": "2026-07-16T15:30:00",
                }
            },
        }

    monkeypatch.setattr(lifecycle, "_read_json", read_exact)
    guard.install(PreviousDailyBase)
    state = PreviousDailyState()

    first = state.rows()[0]
    second = state.rows()[0]

    for row in (first, second):
        assert row["bid_ask_ratio"] == 1.56
        assert row["execution_strength"] == 97.2
        assert row["strength_5m"] == 93.4
        assert row["execution_strength_source"] == "kiwoom_rest_ws_0B_fid228"
        assert row["strength_source"] == "ka10046_rest_lowload"
        assert row["execution_source_trading_date"] == "20260716"
        assert row["strength5_source_trading_date"] == "20260716"
    assert state.status["approved_non_trading_exact_daily_count"] == 1
    assert state.status["approved_non_trading_previous_daily_used_count"] == 1
    assert state.status["approved_non_trading_hold_restored_group_count"] == 3


def test_exact_previous_daily_state_rejects_mismatched_payload_date(monkeypatch):
    monkeypatch.setattr(
        lifecycle,
        "_read_json",
        lambda path: {
            "trading_date": "20260715",
            "codes": {"000660": {"last_valid_execution_strength": 99.9}},
        },
    )

    result = guard._exact_previous_daily_values(lifecycle, "20260716")

    assert result == {}
