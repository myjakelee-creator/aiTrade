from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from realtime_v2 import worker_momentum_accuracy_patch as accuracy

ROOT = Path(__file__).resolve().parents[1]


def test_strict_open_rejects_previous_day_and_accepts_current_session():
    stale = {
        "ohlc": {
            "open": 100,
            "date": "20260719",
            "source": "portable_exact_close",
        }
    }
    assert accuracy._strict_open_meta(stale, "20260720") is None

    current = {
        "momentum_session_day_open": 123,
        "momentum_session_day_open_date": "20260720",
        "momentum_session_day_open_source": "first_accepted_regular_trade",
        "momentum_session_day_open_quality": "first_accepted_regular_trade",
    }
    meta = accuracy._strict_open_meta(current, "20260720")
    assert meta is not None
    assert meta["value"] == 123
    assert meta["date"] == "20260720"


def test_alert_metadata_is_limited_to_current_top100():
    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.name_by_code = {}
            self.quotes = {}
            for rank in range(1, 109):
                code = f"{rank:06d}"
                self.quotes[code] = {
                    "stock_code": code,
                    "stock_name": code,
                    "rank": rank,
                    "trade_value_eok": 1000 - rank,
                }

    metadata = accuracy._top100_metadata(State())
    assert len(metadata) == 100
    assert "000001" in metadata
    assert "000108" not in metadata


def test_production_momentum_accuracy_behavior_in_subprocess():
    script = r'''
import importlib
import threading
from types import SimpleNamespace

production = importlib.import_module("realtime_v2.worker64_guarded_large_bidask")
guarded = importlib.import_module("realtime_v2.worker64_guarded")
momentum = importlib.import_module("realtime_v2.worker_momentum_1m_patch")
engine_module = importlib.import_module("realtime_v2.momentum_badge_engine")
bridge = importlib.import_module("realtime_v2.worker_momentum_accuracy_stage_bridge")

assert production is not None
assert getattr(guarded.base.State, "_stockboard_momentum_accuracy_installed", False)
assert getattr(
    guarded.base.State,
    "_stockboard_momentum_accuracy_stage_bridge_installed",
    False,
)
assert getattr(
    guarded.base.State,
    "_stockboard_momentum_accuracy_stage_owner",
    "",
) == bridge.PATCH_VERSION
assert guarded.base.State.stage_approved_trade_events.__name__ == "stage_events"

MomentumBadgeEngine = engine_module.MomentumBadgeEngine
load_config = engine_module.load_momentum_badge_config

def candle(minute, open_price, high, low, close):
    return {
        "minute_key": minute,
        "minute_text": str(minute),
        "trading_date": "20260720",
        "phase": "regular",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "vwap": None,
        "vwap_valid": False,
        "partial": False,
    }

first = candle(100, 90, 95, 85, 90)
gap = candle(102, 90, 110, 90, 110)
engine = MomentumBadgeEngine(load_config())
engine.observe_completed_candle("000001", first, day_open=100, trading_date="20260720")
engine.observe_completed_candle("000001", gap, day_open=100, trading_date="20260720")
assert not any(item["badge"] == "시돌" for item in engine.badges("000001", 102))

engine = MomentumBadgeEngine(load_config())
engine.observe_completed_candle("000001", first, day_open=100, trading_date="20260720")
next_candle = candle(101, 90, 110, 90, 110)
engine.observe_completed_candle("000001", next_candle, day_open=100, trading_date="20260720")
assert any(item["badge"] == "시돌" for item in engine.badges("000001", 101))
below = candle(103, 95, 99, 90, 95)
engine.observe_completed_candle("000001", below, day_open=100, trading_date="20260720")
assert engine.badges("000001", 103) == []
assert engine.states["000001"]["open"]["exit_minute"] == 103

fixed_session = SimpleNamespace(
    trading_date="20260720",
    calendar_date="20260720",
    phase="regular",
)
momentum._session = lambda: fixed_session
momentum._source_minute = lambda event, _date: (
    20260720 * 1440 + 600,
    10 * 3600 + int(event["second"]),
    "10:00",
)

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self._momentum_accuracy_vwap_tracker = {}
        self._momentum_vwap_scale = {}
        self._momentum_current = {}
        self._momentum_last_completed = {}
        self._momentum_started_minute = -1
        self.daily_values_by_code = {}
        self.quotes = {}

    def _quote(self, code):
        return self.quotes.setdefault(code, {})

state = State()
first_event = {
    "stock_code": "000001",
    "execution_strength_trade_price": 100,
    "cumulative_trade_value_raw": 100000,
    "cumulative_volume": 1000,
    "raw_item": "000001_AL",
    "execution_strength_exchange": "AL",
    "second": 1,
}
second_event = {
    **first_event,
    "cumulative_trade_value_raw": 90000,
    "cumulative_volume": 900,
    "second": 2,
}
momentum.process_events(state, [first_event])
assert state._momentum_current["000001"]["vwap_valid"] is True
momentum.process_events(state, [second_event])
current = state._momentum_current["000001"]
assert current["vwap_valid"] is False
assert current["vwap"] is None
assert current["vwap_invalid_reason"] in {
    "cumulative_value_decreased",
    "cumulative_volume_decreased",
}
print("momentum_accuracy_production_ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "momentum_accuracy_production_ok" in result.stdout
