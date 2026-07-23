from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_next_day_late_fid14_must_replace_previous_close_trade_value():
    """Reproduce the 2026-07-22 premarket/opening mixed-date row.

    Production sequence:

    1. Previous exact-close still contains 144,298 eok and its source date.
    2. The next trading day's first QAx price event has no sampled FID14.
    3. The row becomes realtime while the amount remains previous-day.
    4. A later QAx event carries current-day FID14 6,413 eok.

    QAx does not send a trading_date field. The guard must prove the rollover by
    matching the Worker current trading date with the collector event receive date,
    then comparing it with the older date attached to the held cumulative value.
    """

    script = r'''
import sys
import threading
from types import ModuleType

fake_guarded = ModuleType("realtime_v2.worker64_guarded")
fake_guarded._time_seconds = (
    lambda value: int(str(value)) if str(value or "").isdigit() else None
)


def original_drop(state, quote, code, reason, event, values, trade_time, lag_sec):
    state.status["dropped_trade_count"] = int(
        state.status.get("dropped_trade_count") or 0
    ) + 1
    quote["last_dropped_trade_reason"] = reason


fake_guarded._drop_trade = original_drop
sys.modules["realtime_v2.worker64_guarded"] = fake_guarded

from realtime_v2.worker_trade_field_regression_guard import install


def merged(event):
    result = {}
    if isinstance(event.get("values"), dict):
        result.update(event["values"])
    if isinstance(event.get("kwargs"), dict):
        result.update(event["kwargs"])
    return result


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "trade_count": 0,
            # Simulate one stale status field left by the previous display cycle.
            # The current market date still matches the collector receive date.
            "board_display_current_trading_date": "20260721",
            "market_trading_date": "20260722",
            "board_expected_trading_date": "20260722",
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "row_source": "portable_exact_close",
                "source_trading_date": "20260721",
                "price": 1_850_000.0,
                "change_rate": 4.59,
                "price_trading_date": "20260721",
                "trade_value_eok": 144_298.0,
                "trade_value_trading_date": "20260721",
                "prev_trade_value_eok": 144_298.0,
                "amount_ratio": 1.0,
                "cumulative_volume": 9_000_000,
                "cumulative_volume_trading_date": "20260721",
                "trade_time": "195959",
                "_trade_time_seconds": 195959,
            }
        }

    def _apply_trade(self, event):
        values = merged(event)
        quote = self.quotes[event["stock_code"]]
        raw = values.get("raw") if isinstance(values.get("raw"), dict) else values

        trade_time = values.get("trade_time") or raw.get("trade_time_raw")
        incoming_time = fake_guarded._time_seconds(trade_time)
        incoming_value = values.get("trade_value_eok")
        incoming_volume = values.get("cumulative_volume")

        if quote.get("row_source") == "realtime":
            previous_value = quote.get("trade_value_eok")
            if (
                incoming_value not in (None, "")
                and previous_value not in (None, "")
                and float(incoming_value) + 1.0 < float(previous_value)
            ):
                fake_guarded._drop_trade(
                    self,
                    quote,
                    event["stock_code"],
                    "cumulative_trade_value_decreased",
                    event,
                    values,
                    str(trade_time or ""),
                    None,
                )
                return
            previous_volume = quote.get("cumulative_volume")
            if (
                incoming_volume not in (None, "")
                and previous_volume not in (None, "")
                and int(incoming_volume) < int(previous_volume)
            ):
                fake_guarded._drop_trade(
                    self,
                    quote,
                    event["stock_code"],
                    "cumulative_volume_decreased",
                    event,
                    values,
                    str(trade_time or ""),
                    None,
                )
                return

        price = values.get("price") or raw.get("price_raw")
        if price not in (None, ""):
            quote["price"] = float(price)
            quote["change_rate"] = float(values.get("change_rate") or 0)
            quote["row_source"] = "realtime"

        if trade_time not in (None, ""):
            quote["trade_time"] = str(trade_time)
            quote["_trade_time_seconds"] = incoming_time

        if incoming_value not in (None, ""):
            quote["trade_value_eok"] = float(incoming_value)
            previous = float(quote.get("prev_trade_value_eok") or 0)
            quote["amount_ratio"] = (
                round(float(incoming_value) / previous, 6) if previous > 0 else None
            )

        if incoming_volume not in (None, ""):
            quote["cumulative_volume"] = int(incoming_volume)

        self.status["trade_count"] = int(self.status.get("trade_count") or 0) + 1


class Base:
    State = State
    merged_event_values = staticmethod(merged)


install(Base)
state = State()

# First current-day price: production QAx event has no trading_date and no FID14.
state._apply_trade(
    {
        "type": "trade",
        "ts": "2026-07-22T08:00:01.100+09:00",
        "stock_code": "000660",
        "received_code": "000660_AL",
        "kwargs": {
            "price": "1958000",
            "change_rate": "6.64",
            "trade_time": "080001",
            "source_code": "000660_AL",
        },
    }
)

quote = state.quotes["000660"]
assert quote["row_source"] == "realtime"
assert quote["price"] == 1_958_000.0
assert quote["trade_value_eok"] == 144_298.0
assert quote["trade_value_trading_date"] == "20260721"

# Later sampled FID14 is naturally smaller because the daily cumulative reset.
state._apply_trade(
    {
        "type": "trade",
        "ts": "2026-07-22T08:00:02.200+09:00",
        "stock_code": "000660",
        "received_code": "000660_AL",
        "kwargs": {
            "price": "1959000",
            "change_rate": "6.70",
            "trade_time": "080002",
            "trade_value_eok": "6413",
            "cumulative_volume": "410000",
            "source_code": "000660_AL",
        },
    }
)

quote = state.quotes["000660"]
assert quote["price"] == 1_959_000.0
assert quote["trade_value_eok"] == 6_413.0
assert quote["trade_value_trading_date"] == "20260722"
assert quote["cumulative_volume"] == 410_000
assert quote["cumulative_volume_trading_date"] == "20260722"
assert quote["amount_ratio"] == round(6_413.0 / 144_298.0, 6)
assert state.status.get("dropped_trade_count", 0) == 0
assert state.status.get("trade_field_regression_suppressed_reason_counts") in (None, {})
assert state.status["daily_cumulative_reset_accepted_count"] == 1
assert state.status["trade_field_regression_accepted_reason_counts"] == {
    "daily_cumulative_reset_accepted": 1
}
last = state.status["last_daily_cumulative_reset_accepted"]
assert last["stock_code"] == "000660"
assert last["from_trade_value_date"] == "20260721"
assert last["to_trading_date"] == "20260722"
assert last["trade_value_reset"] is True
assert last["cumulative_volume_reset"] is True
assert state.status["trade_field_regression_guard_version"] == "trade_field_regression_guard_v4"
'''

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
