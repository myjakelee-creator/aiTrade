from __future__ import annotations

import ast
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import realtime_v2.worker_six_metric_lifecycle_patch as module
from realtime_v2.worker_six_metric_lifecycle_patch import (
    GROUPS,
    LIVE_EXECUTION_SOURCE,
    _entry_from_values,
    _entry_valid,
    _group_usable,
    install,
)

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_six_metric_lifecycle_patch.py"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


def _session(phase: str, trading_date: str):
    return SimpleNamespace(
        phase=phase,
        trading_date=trading_date,
        calendar_date=trading_date,
        is_trading_day=True,
        windows={"premarket_start": "08:00"},
    )


class FakeState:
    initial_row = {}

    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.daily_values_by_code = {}
        self.row = deepcopy(self.initial_row)
        self.daily_dirty = False
        self.persist_count = 0

    def rows(self, limit=300):
        return [deepcopy(self.row)][:limit]

    def persist_daily_state_if_needed(self, force=False):
        self.persist_count += 1
        return bool(force or self.daily_dirty)


class FakeBase:
    State = FakeState
    DAILY_PERSIST_KEYS = ()


def _full_row():
    return {
        "stock_code": "000660",
        "trade_value_eok": 200.0,
        "prev_trade_value_eok": 100.0,
        "amount_ratio": 2.0,
        "received_at": "2026-07-15T15:30:00+09:00",
        "bid_ask_ratio": 1.5,
        "bid_volume": 600,
        "ask_volume": 400,
        "orderbook_source": "ka10004_rest_lowload",
        "orderbook_status": "ok",
        "orderbook_received_at": "2026-07-15T15:30:01+09:00",
        "execution_strength": 95.04,
        "execution_strength_source": LIVE_EXECUTION_SOURCE,
        "execution_strength_status": "ok",
        "execution_strength_received_at": "2026-07-15T15:30:02+09:00",
        "strength_5m": 101.2,
        "strength_source": "ka10046_rest_lowload",
        "strength_status": "ok",
        "strength_snapshot_at": "2026-07-15T15:30:03+09:00",
        "program_net": 0.0,
        "program_net_source": "ka90004_tr_singleflight",
        "program_net_status": "ok",
        "program_net_updated_at": "2026-07-15T15:30:04+09:00",
        "large_trade_buy_count": 5,
        "large_trade_sell_count": 2,
        "large_trade_net_count": 3,
        "large_trade_buy_sum_eok": 4.0,
        "large_trade_sell_sum_eok": 1.0,
        "large_trade_net_sum_eok": 3.0,
        "large_trade_source": "collector_aggregate",
        "large_trade_status": "ok",
        "large_trade_updated_at": "2026-07-15T15:30:05+09:00",
    }


def test_group_rules_distinguish_valid_zero_and_invalid_execution_source():
    row = _full_row()

    assert _group_usable(row, "amount_ratio") is True
    assert _group_usable(row, "orderbook") is True
    assert _group_usable(row, "execution") is True
    assert _group_usable(row, "strength5") is True
    assert _group_usable(row, "program") is True
    assert _group_usable(row, "large_trade") is True

    wrong_execution = dict(row)
    wrong_execution["execution_strength_source"] = "ka10046_rest_lowload"
    assert _group_usable(wrong_execution, "execution") is False

    zero_ratio = dict(row)
    zero_ratio["amount_ratio"] = 0
    zero_ratio["trade_value_eok"] = 0
    assert _group_usable(zero_ratio, "amount_ratio") is False


def test_entry_requires_matching_trading_date_and_expires(monkeypatch):
    monkeypatch.setattr(
        module,
        "_next_premarket_boundary",
        lambda now=None: datetime(2026, 7, 16, 8, 0, 0),
    )
    entry = _entry_from_values(
        "000660",
        "orderbook",
        _full_row(),
        source_date="20260715",
        now=datetime(2026, 7, 15, 20, 0, 0),
    )

    assert entry is not None
    assert _entry_valid(
        entry,
        expected_date="20260715",
        group="orderbook",
        now=datetime(2026, 7, 16, 7, 59, 59),
    ) is True
    assert _entry_valid(
        entry,
        expected_date="20260716",
        group="orderbook",
        now=datetime(2026, 7, 16, 7, 59, 59),
    ) is False
    assert _entry_valid(
        entry,
        expected_date="20260715",
        group="orderbook",
        now=datetime(2026, 7, 16, 8, 0, 0),
    ) is False


def test_aftermarket_missing_values_use_same_session_cache_and_restart_hold(
    monkeypatch,
    tmp_path,
):
    snapshot_path = tmp_path / "six_metric.json"
    monkeypatch.setattr(module, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(module, "SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(module, "SESSION_HOLD_PATH", tmp_path / "session_hold.json")
    monkeypatch.setattr(
        module,
        "next_premarket_datetime",
        lambda now=None: datetime(2099, 1, 2, 8, 0, 0),
    )
    current_phase = {"value": "aftermarket", "date": "20260715"}
    monkeypatch.setattr(
        module,
        "market_session_now",
        lambda now=None: _session(current_phase["value"], current_phase["date"]),
    )
    monkeypatch.setattr(
        module,
        "last_completed_trading_date",
        lambda now=None: "20260715",
    )

    class LocalState(FakeState):
        initial_row = _full_row()

    class LocalBase:
        State = LocalState
        DAILY_PERSIST_KEYS = ()

    install(LocalBase)
    state = LocalState()
    first = state.rows()[0]
    state.flush_six_metric_lifecycle(force=True)

    assert first["amount_ratio"] == 2.0
    assert first["program_net"] == 0.0
    assert snapshot_path.is_file()
    assert state.status["six_metric_amount_ratio_live_count"] == 1
    assert state.status["six_metric_large_trade_live_count"] == 1

    state.row = {"stock_code": "000660", "prev_trade_value_eok": 100.0}
    held = state.rows()[0]

    assert held["amount_ratio"] == 2.0
    assert held["bid_ask_ratio"] == 1.5
    assert held["execution_strength"] == 95.04
    assert held["strength_5m"] == 101.2
    assert held["program_net"] == 0.0
    assert held["large_trade_net_count"] == 3
    assert held["amount_ratio_display_basis"] == "same_session_last_valid"
    assert state.status["six_metric_amount_ratio_held_count"] == 1

    current_phase.update(value="before_market", date="20260716")
    restarted = LocalState()
    restarted.row = {"stock_code": "000660", "prev_trade_value_eok": 100.0}
    previous_final = restarted.rows()[0]

    assert previous_final["amount_ratio"] == 2.0
    assert previous_final["large_trade_net_count"] == 3
    assert previous_final["amount_ratio_display_basis"] == (
        "previous_session_final_until_premarket"
    )
    assert previous_final["program_net_status"] == "previous_session_final_hold"


def test_premarket_does_not_reuse_previous_session_cache(monkeypatch, tmp_path):
    snapshot_path = tmp_path / "six_metric.json"
    monkeypatch.setattr(module, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(module, "SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(module, "SESSION_HOLD_PATH", tmp_path / "session_hold.json")
    monkeypatch.setattr(
        module,
        "market_session_now",
        lambda now=None: _session("premarket", "20260716"),
    )
    monkeypatch.setattr(
        module,
        "last_completed_trading_date",
        lambda now=None: "20260715",
    )

    class LocalState(FakeState):
        initial_row = {"stock_code": "000660"}

    class LocalBase:
        State = LocalState
        DAILY_PERSIST_KEYS = ()

    install(LocalBase)
    row = LocalState().rows()[0]

    for group in GROUPS:
        for key in GROUPS[group]["display_keys"]:
            assert key not in row


def test_lifecycle_patch_adds_no_thread_network_qax_or_browser_calculation():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "Thread(",
        "requests.",
        "urlopen(",
        "websocket",
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "document.",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source
