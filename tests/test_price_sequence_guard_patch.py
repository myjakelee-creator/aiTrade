from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.collector32 import EventSender, PublishingStore
from realtime_v2 import price_sequence_guard_patch as patch

ROOT = Path(__file__).resolve().parents[1]


class CapturingSender:
    def __init__(self):
        self.events = []

    def publish_trade(self, event):
        self.events.append(event)


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"trade_count": 0}
        self.quotes = {}

    def _apply_trade(self, event):
        values = {}
        values.update(event.get("values") or {})
        values.update(event.get("kwargs") or {})
        raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
        code = event["stock_code"]
        quote = self.quotes.setdefault(code, {"stock_code": code})
        quote["price"] = int(str(raw["price_raw"]).replace("+", "").replace("-", ""))
        quote["trade_price"] = quote["price"]
        quote["change_rate"] = float(raw["change_rate_raw"])
        quote["received_at"] = event["ts"]
        quote["trade_value_eok"] = float(values["trade_value_eok"])
        quote["cumulative_volume"] = int(values["cumulative_volume"])
        self.status["trade_count"] += 1


def _base():
    def merged_event_values(event):
        values = {}
        values.update(event.get("values") or {})
        values.update(event.get("kwargs") or {})
        return values

    return SimpleNamespace(State=FakeState, merged_event_values=merged_event_values)


def _event(epoch, seq, price, value, volume, ts):
    return {
        "type": "trade",
        "ts": ts,
        "stock_code": "005930",
        "collector_price_epoch": epoch,
        "collector_price_seq": seq,
        "kwargs": {
            "collector_price_epoch": epoch,
            "collector_price_seq": seq,
            "trade_value_eok": value,
            "cumulative_volume": volume,
            "raw": {
                "price_raw": f"+{price}",
                "change_rate_raw": "4.03",
            },
        },
    }


def test_publishing_store_assigns_monotonic_seq_and_stable_epoch():
    sender = CapturingSender()
    store = PublishingStore(sender)

    store.update_trade("005930", raw={"price_raw": "+271000"})
    store.update_trade("000660", raw={"price_raw": "+1900000"})

    assert [event["collector_price_seq"] for event in sender.events] == [1, 2]
    assert sender.events[0]["collector_price_epoch"] == sender.events[1]["collector_price_epoch"]
    assert sender.events[0]["kwargs"]["collector_price_seq"] == 1


def test_sender_requeue_keeps_newer_sequence_for_same_symbol():
    sender = EventSender("127.0.0.1", 1)
    newer = _event("epoch-a", 12, 271000, 100.0, 1000, "2026-07-23T15:30:12+09:00")
    older = _event("epoch-a", 11, 270500, 99.0, 990, "2026-07-23T15:30:11+09:00")

    sender.latest_trade_by_code["005930"] = newer
    sender._requeue_unsent([older])

    assert sender.latest_trade_by_code["005930"]["collector_price_seq"] == 12


def test_older_sequence_restores_price_but_allows_cumulative_fields():
    base = _base()
    patch._install_state_wrapper(base)
    state = base.State()

    state._apply_trade(_event("epoch-a", 12, 271000, 100.0, 1000, "2026-07-23T15:30:12+09:00"))
    state._apply_trade(_event("epoch-a", 11, 269500, 101.0, 1010, "2026-07-23T15:30:13+09:00"))

    quote = state.quotes["005930"]
    assert quote["price"] == 271000
    assert quote["trade_price"] == 271000
    assert quote["change_rate"] == 4.03
    assert quote["received_at"] == "2026-07-23T15:30:12+09:00"
    assert quote["trade_value_eok"] == 101.0
    assert quote["cumulative_volume"] == 1010
    assert quote["collector_price_seq"] == 12
    assert state.status["price_sequence_suppressed_count"] == 1
    assert state.status["price_sequence_guard_version"] == "price_sequence_guard_v1"


def test_new_epoch_accepts_sequence_restart():
    base = _base()
    patch._install_state_wrapper(base)
    state = base.State()

    state._apply_trade(_event("epoch-a", 100, 271000, 100.0, 1000, "2026-07-23T15:30:12+09:00"))
    state._apply_trade(_event("epoch-b", 1, 272000, 101.0, 1010, "2026-07-23T15:31:00+09:00"))

    quote = state.quotes["005930"]
    assert quote["price"] == 272000
    assert quote["collector_price_epoch"] == "epoch-b"
    assert quote["collector_price_seq"] == 1


def test_missing_sequence_is_accepted_for_compatibility():
    base = _base()
    patch._install_state_wrapper(base)
    state = base.State()
    event = _event(None, None, 271000, 100.0, 1000, "2026-07-23T15:30:12+09:00")
    event.pop("collector_price_epoch")
    event.pop("collector_price_seq")
    event["kwargs"].pop("collector_price_epoch")
    event["kwargs"].pop("collector_price_seq")

    state._apply_trade(event)

    assert state.quotes["005930"]["price"] == 271000
    assert state.status.get("price_sequence_suppressed_count") is None


def test_runtime_init_installs_sequence_guard_without_monotonic_guard():
    source = Path(patch.__file__).with_name("__init__.py").read_text(encoding="utf-8")
    assert "install_price_sequence_guard()" in source
    assert "install_price_time_monotonic" not in source
    assert source.index("install_premarket_rollover_priority()") < source.index(
        "install_price_sequence_guard()"
    ) < source.index("install_sse_latest_only()")


def test_production_worker_import_has_sequence_guard_installed():
    script = r'''
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base

assert getattr(base.State, "_stockboard_price_sequence_guard_installed", False) is True
assert getattr(base.State, "_stockboard_price_sequence_guard_version", None) == "price_sequence_guard_v1"
print("price_sequence_guard_production_import_ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "price_sequence_guard_production_import_ok" in completed.stdout
