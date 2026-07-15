from __future__ import annotations

import ast
import threading
from pathlib import Path

from realtime_v2.worker_rest_live_metrics_stage2_fix_patch import install

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_rest_live_metrics_stage2_fix_patch.py"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {
            "000001": {"stock_code": "000001", "trade_value_eok": 1000},
            "000002": {"stock_code": "000002", "trade_value_eok": 900},
        }
        self.daily_values_by_code = {}

    def rows(self, limit=300):
        return [dict(value) for value in self.quotes.values()][:limit]


class FakeUpdater:
    def __init__(self, state):
        self.state = state
        self.selected_code = ""
        self.strength_source_signature_by_code = {}

    def _refresh_selected(self):
        return None

    def _apply(self, code, metric, payload):
        if metric != "strength":
            return True
        row = payload["cntr_str_tm"][0]
        value = float(row["cntr_str"])
        timestamp = "2026-07-15T17:10:00+09:00"
        quote = self.state.quotes.setdefault(code, {"stock_code": code})
        daily = self.state.daily_values_by_code.setdefault(code, {})
        for target in (quote, daily):
            target["execution_strength"] = value
            target["execution_strength_updated_at"] = timestamp
        return True


class FakeModule:
    RestLiveMetricUpdater = FakeUpdater
    PERSIST_KEYS = ()


class FakeBase:
    State = FakeState
    DAILY_PERSIST_KEYS = ()


def _install_with_module(monkeypatch):
    import realtime_v2.worker_rest_live_metrics_patch as real_module

    monkeypatch.setattr(real_module, "RestLiveMetricUpdater", FakeUpdater)
    monkeypatch.setattr(real_module, "PERSIST_KEYS", ())
    install(FakeBase)


def test_missing_selection_falls_back_to_trade_value_top1(monkeypatch):
    _install_with_module(monkeypatch)
    state = FakeState()
    updater = FakeUpdater(state)

    updater._refresh_selected()

    assert updater.selected_code == "000001"
    assert state.status["rest_live_metrics_selected_code"] == "000001"
    assert state.status["rest_live_metrics_selected_source"] == "trade_value_top1_fallback"


def test_worker_status_selection_beats_top1_fallback(monkeypatch):
    _install_with_module(monkeypatch)
    state = FakeState()
    state.status["selected_code"] = "000002"
    updater = FakeUpdater(state)

    updater._refresh_selected()

    assert updater.selected_code == "000002"
    assert state.status["rest_live_metrics_selected_source"] == "status:selected_code"


def test_same_source_row_keeps_real_change_time_and_tracks_poll(monkeypatch):
    _install_with_module(monkeypatch)
    state = FakeState()
    updater = FakeUpdater(state)
    payload = {"cntr_str_tm": [{"cntr_tm": "171000", "cntr_str": "101.25"}]}

    assert updater._apply("000001", "strength", payload) is True
    first_updated_at = state.quotes["000001"]["execution_strength_updated_at"]
    first_polled_at = state.quotes["000001"]["execution_strength_polled_at"]

    assert updater._apply("000001", "strength", payload) is True

    assert state.quotes["000001"]["execution_strength_source_time"] == "171000"
    assert state.quotes["000001"]["execution_strength_updated_at"] == first_updated_at
    assert state.quotes["000001"]["execution_strength_polled_at"] >= first_polled_at
    assert state.status["rest_live_strength_unchanged_count"] == 1


def test_new_source_row_counts_as_real_change(monkeypatch):
    _install_with_module(monkeypatch)
    state = FakeState()
    updater = FakeUpdater(state)

    updater._apply(
        "000001",
        "strength",
        {"cntr_str_tm": [{"cntr_tm": "171000", "cntr_str": "101.25"}]},
    )
    updater._apply(
        "000001",
        "strength",
        {"cntr_str_tm": [{"cntr_tm": "171030", "cntr_str": "102.50"}]},
    )

    assert state.status["rest_live_strength_changed_count"] == 2
    assert state.status["rest_live_strength_last_changed_code"] == "000001"
    assert state.quotes["000001"]["execution_strength_source_time"] == "171030"


def test_stage2_fix_adds_no_network_qax_thread_or_price_callback_work():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "requests.",
        "urlopen(",
        "Thread(",
        "subprocess",
        "Start-Process",
        "document.",
        "fetch(",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source
