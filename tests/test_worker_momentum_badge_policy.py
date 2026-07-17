from __future__ import annotations

import threading
from copy import deepcopy

import realtime_v2.worker_momentum_badge_policy_patch as policy
from realtime_v2.momentum_badge_engine import load_momentum_badge_config


def candle(minute, *, open_price, high, low, close, vwap):
    return {
        "minute_key": minute,
        "trading_date": "20260720",
        "phase": "regular",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "vwap": vwap,
        "partial": False,
    }


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.name_by_code = {"123456": "하단종목"}
        self.seed_rank_by_code = {"123456": 287}
        self.quotes = {
            "123456": {
                "stock_code": "123456",
                "stock_name": "하단종목",
                "rank": 287,
                "day_open": 100,
            }
        }
        self.daily_values_by_code = {
            "123456": {
                "day_open": 100,
                "momentum_last_completed_candle": candle(
                    10,
                    open_price=99,
                    high=101,
                    low=98,
                    close=99,
                    vwap=100,
                ),
            }
        }
        self.rebuilds = []
        self.dirty = False

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.dirty = True

    def request_background_rebuild(self, *, reason, force=False):
        self.rebuilds.append((reason, force))

    def stage_approved_trade_events(self, events):
        return None

    def rows(self, limit=300):
        return [deepcopy(value) for value in self.quotes.values()][:limit]

    def reset_approved_minute_pipeline_for_date(self, target, phase):
        return None


class FakeHandler:
    def do_GET(self):
        return None


class FakeBase:
    State = FakeState
    WebHandler = FakeHandler
    DAILY_PERSIST_KEYS = ()


def test_policy_suppresses_candle_rebuild_and_surfaces_low_rank_alert(monkeypatch):
    mutable_minute = {"value": 11}
    monkeypatch.setattr(policy.momentum_1m, "install", lambda base: None)
    monkeypatch.setattr(policy.momentum_1m, "_expected_date", lambda now=None: "20260720")
    monkeypatch.setattr(
        policy.momentum_1m,
        "_system_minute_key",
        lambda now=None: mutable_minute["value"],
    )
    monkeypatch.setattr(
        policy.momentum_1m,
        "_nested_open",
        lambda source: source.get("day_open"),
    )
    monkeypatch.setattr(policy, "load_momentum_badge_config", load_momentum_badge_config)

    class LocalState(FakeState):
        pass

    class LocalHandler(FakeHandler):
        pass

    class LocalBase:
        State = LocalState
        WebHandler = LocalHandler
        DAILY_PERSIST_KEYS = ()

    policy.install(LocalBase)
    state = LocalState()

    state.request_background_rebuild(reason="momentum_1m_signal", force=False)
    assert state.rebuilds == []
    assert state.status["momentum_badge_suppressed_candle_rebuild_count"] == 1

    current = candle(11, open_price=101, high=104, low=100, close=103, vwap=102)
    state.daily_values_by_code["123456"]["momentum_last_completed_candle"] = current
    state.quotes["123456"]["momentum_last_completed_candle"] = current
    state.stage_approved_trade_events([{"stock_code": "123456"}])

    row = state.rows()[0]
    assert [item["badge"] for item in row["momentum_badges"]] == ["시돌", "중돌"]
    assert state.rebuilds == [("momentum_badge_signal_change", False)]
    assert state.status["momentum_badge_extra_qax_fids"] == 0
    assert state.status["momentum_badge_extra_rest_requests"] == 0
    assert state.status["momentum_badge_extra_websockets"] == 0
    assert state.status["momentum_badge_extra_threads"] == 0

    alerts = state.momentum_alert_payload()
    assert alerts["count"] == 1
    assert alerts["items"][0]["rank"] == 287
    assert [item["badge"] for item in alerts["items"][0]["badges"]] == ["시돌", "중돌"]
    assert "momentum_badge_state" in LocalBase.DAILY_PERSIST_KEYS
