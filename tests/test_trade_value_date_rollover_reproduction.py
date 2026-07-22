from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_next_day_late_fid14_must_replace_previous_close_trade_value():
    """Reproduce the 2026-07-22 premarket/opening mixed-date row.

    Sequence observed on the owner PC:

    1. The previous exact-close row still contains 144,298 eok.
    2. The next trading day's first price event arrives without sampled FID14.
    3. The row becomes current-day realtime while the amount is still previous-day.
    4. A later current-day FID14 arrives with 6,413 eok.

    A trading-day reset is not a same-day cumulative regression. The current-day
    amount, volume, date and ratio must replace the previous-day display values.
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
        self.status = {}
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "row_source": "portable_exact_close",
                "price": 1_850_000.0,
                "change_rate": 4.59,
                "price_trading_date": "20260721",
                "trade_value_eok": 144_298.0,
                "trade_value_trading_date": "20260721",
                "prev_trade_value_eok": 144_298.0,
                "cumulative_volume": 9_000_000,
                "trade_time": "195959",
                "_trade_time_seconds": 195959,
            }
        }

    def _apply_trade(self, event):
        values = merged(event)
        quote = self.quotes[event["stock_code"]]
        raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
        trading_date = str(values.get("trading_date") or "")

        price = values.get("price") or raw.get("price_raw")
        if price not in (None, ""):
            quote["price"] = float(price)
            quote["change_rate"] = float(values.get("change_rate") or 0)
            quote["row_source"] = "realtime"
            quote["price_trading_date"] = trading_date

        trade_time = values.get("trade_time") or raw.get("trade_time_raw")
        if trade_time not in (None, ""):
            quote["trade_time"] = str(trade_time)
            quote["_trade_time_seconds"] = fake_guarded._time_seconds(trade_time)

        trade_value = values.get("trade_value_eok")
        if trade_value not in (None, ""):
            quote["trade_value_eok"] = float(trade_value)
            quote["trade_value_trading_date"] = trading_date
            previous = float(quote.get("prev_trade_value_eok") or 0)
            quote["amount_ratio"] = (
                round(float(trade_value) / previous, 6) if previous > 0 else None
            )

        cumulative_volume = values.get("cumulative_volume")
        if cumulative_volume not in (None, ""):
            quote["cumulative_volume"] = int(cumulative_volume)


class Base:
    State = State
    merged_event_values = staticmethod(merged)


install(Base)
state = State()

# First current-day price: FID14 has not been sampled yet.
state._apply_trade(
    {
        "type": "trade",
        "stock_code": "000660",
        "received_code": "000660_AL",
        "kwargs": {
            "price": "1958000",
            "change_rate": "6.64",
            "trade_time": "080001",
            "trading_date": "20260722",
            "source_code": "000660_AL",
        },
    }
)

quote = state.quotes["000660"]
assert quote["row_source"] == "realtime"
assert quote["price_trading_date"] == "20260722"
assert quote["trade_value_eok"] == 144_298.0
assert quote["trade_value_trading_date"] == "20260721"

# Later current-day FID14 is naturally smaller because the daily cumulative reset.
state._apply_trade(
    {
        "type": "trade",
        "stock_code": "000660",
        "received_code": "000660_AL",
        "kwargs": {
            "price": "1959000",
            "change_rate": "6.70",
            "trade_time": "080002",
            "trade_value_eok": "6413",
            "cumulative_volume": "410000",
            "trading_date": "20260722",
            "source_code": "000660_AL",
        },
    }
)

quote = state.quotes["000660"]
assert quote["price"] == 1_959_000.0
assert quote["trade_value_eok"] == 6_413.0, (
    "RED reproduction: next-day FID14 was suppressed as a same-day regression; "
    f"actual={quote['trade_value_eok']} expected=6413.0"
)
assert quote["trade_value_trading_date"] == "20260722"
assert quote["cumulative_volume"] == 410_000
assert quote["amount_ratio"] == round(6_413.0 / 144_298.0, 6)
assert not state.status.get("trade_field_regression_suppressed_reason_counts"), (
    "A verified next-trading-day cumulative reset must not be counted as "
    "cumulative_trade_value_decreased."
)
'''

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    # This assertion is intentionally red on the frozen baseline. Production code
    # must not be changed until the owner approves the isolated fix design.
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
