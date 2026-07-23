from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_verified_new_day_rollover_accepts_first_event_on_exact_close_hold():
    _run(
        r'''
import sys, threading
from types import ModuleType

fake_guarded = ModuleType("realtime_v2.worker64_guarded")
fake_guarded._time_seconds = lambda value: int(str(value)) if str(value or "").isdigit() else None
fake_guarded._drop_trade = lambda *args, **kwargs: None
sys.modules["realtime_v2.worker64_guarded"] = fake_guarded

from realtime_v2 import worker_trade_field_regression_guard as guard
from realtime_v2.premarket_rollover_priority_patch import install_runtime_wrapper


def merged(event):
    return dict(event.get("kwargs") or {})


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "trade_count": 0,
            "market_trading_date": "20260723",
            "board_display_current_trading_date": "20260723",
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "row_source": "portable_exact_close",
                "source_trading_date": "20260722",
                "price": 1_850_000.0,
                "change_rate": 0.0,
                "trade_value_eok": 153_991.28,
                "trade_value_trading_date": "20260722",
                "cumulative_volume": 8_000_000,
                "cumulative_volume_trading_date": "20260722",
                "trade_time": "195959",
                "_trade_time_seconds": 195959,
                "prev_trade_value_eok": 1_950.0,
            }
        }

    def _apply_trade(self, event):
        values = merged(event)
        quote = self.quotes[event["stock_code"]]
        incoming_time = fake_guarded._time_seconds(values.get("trade_time"))
        previous_time = quote.get("_trade_time_seconds")
        if incoming_time is not None and previous_time is not None and incoming_time < previous_time:
            fake_guarded._drop_trade(self, quote, event["stock_code"], "older_fid20_than_last_accepted", event, values, values.get("trade_time"), None)
            return
        if values.get("price") not in (None, ""):
            quote["price"] = abs(float(values["price"]))
            quote["change_rate"] = float(values.get("change_rate") or 0)
            quote["row_source"] = "realtime"
        quote["trade_time"] = str(values.get("trade_time"))
        quote["_trade_time_seconds"] = incoming_time
        if values.get("trade_value_eok") not in (None, ""):
            quote["trade_value_eok"] = float(values["trade_value_eok"])
            quote["amount_ratio"] = round(quote["trade_value_eok"] / quote["prev_trade_value_eok"], 6)
        if values.get("cumulative_volume") not in (None, ""):
            quote["cumulative_volume"] = int(values["cumulative_volume"])
        self.status["trade_count"] += 1

    def rows(self, limit=300):
        return [dict(value) for value in self.quotes.values()][:limit]


class Base:
    State = State
    merged_event_values = staticmethod(merged)


install_runtime_wrapper()
guard.install(Base)
state = State()
state._apply_trade({
    "type": "trade",
    "ts": "2026-07-23T08:05:10+09:00",
    "stock_code": "000660",
    "received_code": "000660",
    "kwargs": {
        "price": "1879000",
        "change_rate": "2.68",
        "trade_time": "080510",
        "trade_value_eok": "3159.74",
        "cumulative_volume": "170000",
        "original_registered_code": "000660_AL",
    },
})
quote = state.quotes["000660"]
assert quote["row_source"] == "realtime"
assert quote["price"] == 1_879_000.0
assert quote["trade_value_eok"] == 3159.74
assert quote["trade_value_trading_date"] == "20260723"
assert quote["cumulative_volume"] == 170000
assert quote["cumulative_volume_trading_date"] == "20260723"
assert quote["trade_time"] == "080510"
assert quote["_trade_time_seconds"] == 80510
assert quote["amount_ratio"] == round(3159.74 / 1950.0, 6)
assert state.status["daily_cumulative_reset_accepted_count"] == 1
assert state.status["premarket_rollover_time_wrap_accepted_count"] == 1
assert state.status["trade_field_regression_guard_version"] == "trade_field_regression_guard_v5"
assert getattr(Base.State, "_stockboard_current_day_trade_value_rank_installed", False) is True
'''
    )


def test_current_day_ranking_excludes_previous_day_hold_amounts():
    _run(
        r'''
import threading
from realtime_v2.current_day_trade_value_rank_patch import install


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"market_trading_date": "20260723"}
        self.payload = [
            {
                "stock_code": "003230",
                "stock_name": "삼양식품",
                "rank": 1,
                "row_source": "portable_exact_close",
                "source_trading_date": "20260722",
                "trade_value_trading_date": "20260722",
                "trade_value_eok": 620.63,
                "amount_ratio": 0.65,
                "prev_rank": 8,
            },
            {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "rank": 2,
                "row_source": "realtime",
                "source_trading_date": "20260722",
                "trade_value_trading_date": "20260723",
                "trade_value_eok": 5506.18,
                "amount_ratio": 0.051,
            },
            {
                "stock_code": "000660",
                "stock_name": "SK하이닉스",
                "rank": 3,
                "row_source": "realtime",
                "source_trading_date": "20260722",
                "trade_value_trading_date": "20260723",
                "trade_value_eok": 8893.22,
                "amount_ratio": 0.3,
            },
        ]

    def rows(self, limit=300):
        return [dict(row) for row in self.payload][:limit]


class Base:
    State = State


install(Base)
state = State()
rows = state.rows(3)
assert [row["stock_code"] for row in rows] == ["000660", "005930", "003230"]
assert [row["rank"] for row in rows[:2]] == [1, 2]
assert rows[2]["rank"] is None
assert rows[2]["trade_value_eok"] is None
assert rows[2]["held_trade_value_eok"] == 620.63
assert rows[2]["amount_ratio"] is None
assert state.status["current_day_trade_value_rank_eligible_count"] == 2
assert state.status["current_day_trade_value_rank_held_count"] == 1
'''
    )
