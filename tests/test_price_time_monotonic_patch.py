from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2 import price_time_monotonic_patch as patch


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"trade_count": 0}
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "row_source": "realtime",
                "price": 271000,
                "trade_price": 271000,
                "change_rate": 4.03,
                "trade_time": "100303",
                "_trade_time_seconds": 36183,
                "received_at": "2026-07-23T10:03:03.888+09:00",
                "price_age_sec": 1.0,
                "trade_value_eok": 23000.0,
                "cumulative_volume": 1000000,
                "source_code": "005930_AL",
            }
        }

    def _apply_trade(self, event):
        values = {}
        values.update(event.get("values") or {})
        values.update(event.get("kwargs") or {})
        raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
        quote = self.quotes["005930"]
        quote["price"] = int(str(raw.get("price_raw")).replace("+", ""))
        quote["trade_price"] = quote["price"]
        quote["change_rate"] = float(raw.get("change_rate_raw"))
        quote["trade_time"] = raw.get("trade_time_raw")
        quote["_trade_time_seconds"] = 35200
        quote["received_at"] = event.get("ts")
        quote["price_age_sec"] = 999.0
        quote["source_code"] = values.get("source_code")
        quote["trade_value_eok"] = float(values.get("trade_value_eok"))
        quote["cumulative_volume"] = int(values.get("cumulative_volume"))
        self.status["trade_count"] += 1


def _base():
    def merged_event_values(event):
        result = {}
        result.update(event.get("values") or {})
        result.update(event.get("kwargs") or {})
        return result

    return SimpleNamespace(State=FakeState, merged_event_values=merged_event_values)


def _event(ts: str, price: int, rate: float, value: float, volume: int):
    return {
        "type": "trade",
        "ts": ts,
        "stock_code": "005930",
        "received_code": "005930",
        "kwargs": {
            "source_code": "005930_AL",
            "trade_value_eok": value,
            "cumulative_volume": volume,
            "raw": {
                "price_raw": f"+{price}",
                "change_rate_raw": f"{rate}",
                "trade_time_raw": "095000",
            },
        },
    }


def test_older_event_keeps_latest_price_timestamp_but_allows_cumulative_apply():
    base = _base()
    patch._install_state_wrapper(base)
    state = base.State()

    state._apply_trade(
        _event(
            "2026-07-23T09:48:25.993+09:00",
            269500,
            3.45,
            23100.0,
            1010000,
        )
    )

    quote = state.quotes["005930"]
    assert quote["price"] == 271000
    assert quote["trade_price"] == 271000
    assert quote["change_rate"] == 4.03
    assert quote["received_at"] == "2026-07-23T10:03:03.888+09:00"
    assert quote["trade_time"] == "100303"
    assert quote["_trade_time_seconds"] == 36183
    assert quote["trade_value_eok"] == 23100.0
    assert quote["cumulative_volume"] == 1010000
    assert state.status["trade_count"] == 1
    assert state.status["stale_price_event_suppressed_count"] == 1
    assert state.status["price_time_monotonic_version"] == "price_time_monotonic_v1"
    assert state.status["last_stale_price_event_suppressed"]["reason"] == (
        "event_received_at_older_than_quote"
    )


def test_newer_event_updates_price_timestamp_normally():
    base = _base()
    patch._install_state_wrapper(base)
    state = base.State()

    state._apply_trade(
        _event(
            "2026-07-23T10:03:04.100+09:00",
            271500,
            4.22,
            23200.0,
            1020000,
        )
    )

    quote = state.quotes["005930"]
    assert quote["price"] == 271500
    assert quote["change_rate"] == 4.22
    assert quote["received_at"] == "2026-07-23T10:03:04.100+09:00"
    assert state.status.get("stale_price_event_suppressed_count") is None


def test_runtime_init_installs_monotonic_wrapper_after_rollover_wrapper():
    source = (patch.__file__ and __import__("pathlib").Path(patch.__file__).with_name("__init__.py").read_text(encoding="utf-8"))
    assert source.index("install_premarket_rollover_priority()") < source.index(
        "install_price_time_monotonic()"
    )
