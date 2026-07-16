from __future__ import annotations

import ast
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import realtime_v2.worker_market_metric_session_manager as manager

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_market_metric_session_manager.py"
CONFIG_PATH = ROOT / "configs" / "stockboard_live_metrics_rest.json"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


def _session(phase: str, trading_date: str, regular_start: str = "09:00"):
    return SimpleNamespace(
        phase=phase,
        trading_date=trading_date,
        calendar_date=trading_date,
        is_trading_day=phase not in {"weekend", "holiday"},
        windows={"regular_start": regular_start, "premarket_start": "08:00"},
    )


def test_delayed_open_uses_calendar_regular_start_for_burst(monkeypatch):
    monkeypatch.setattr(
        manager,
        "market_session_now",
        lambda now=None: _session("regular", "20260716", "10:00"),
    )
    config = {"session_manager": {"opening_burst_minutes": 10}}

    assert manager.market_metric_phase(
        now=manager.datetime(2026, 7, 16, 10, 5),
        config=config,
    ) == "opening_burst"
    assert manager.market_metric_phase(
        now=manager.datetime(2026, 7, 16, 10, 11),
        config=config,
    ) == "regular"


def test_closed_and_before_market_target_last_completed_date(monkeypatch):
    current = {"phase": "before_market"}
    monkeypatch.setattr(
        manager,
        "market_session_now",
        lambda now=None: _session(current["phase"], "20260716"),
    )
    monkeypatch.setattr(
        manager,
        "last_completed_trading_date",
        lambda now=None: "20260715",
    )

    assert manager.metric_target_trading_date() == "20260715"
    current["phase"] = "premarket"
    assert manager.metric_target_trading_date() == "20260716"


def test_metric_completion_requires_expected_date_and_valid_source():
    assert manager._metric_complete(
        {
            "bid_ask_ratio": 1.5,
            "orderbook_source_trading_date": "20260715",
        },
        "bidask",
        "20260715",
    ) is True
    assert manager._metric_complete(
        {
            "bid_ask_ratio": 1.5,
            "orderbook_source_trading_date": "20260713",
        },
        "bidask",
        "20260715",
    ) is False
    assert manager._metric_complete(
        {
            "large_trade_net_count": 0,
            "large_trade_source": "kiwoom_rest_ws_0B_fid15",
            "large_trade_source_trading_date": "20260715",
        },
        "large_trade",
        "20260715",
    ) is True


def test_config_has_top100_completion_and_opening_protection():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    policies = config["session_manager"]["phase_policies"]

    assert config["max_top_codes"] == 100
    assert config["query_suffix_by_session"]["aftermarket"] == "_AL"
    assert policies["opening_burst"]["scope"] == 100
    assert policies["opening_burst"]["min_request_gap_sec"] == 3.0
    assert config["approved_minute_pipeline"]["strength_open_delay_minutes"] == 5
    assert policies["after_wait"]["scope"] == 100
    assert policies["aftermarket"]["scope"] == 100
    assert policies["closed"]["scope"] == 100
    assert policies["weekend"]["active"] is False
    assert policies["holiday"]["active"] is False

    # Active-session ka10004 and ka10055 polling are replaced by one auxiliary stream.
    # The one REST lane is reserved for 100 ka10046 calls over each 300-second cycle.
    assert policies["regular"]["intervals"]["bidask"]["top100"] == 0
    assert policies["regular"]["intervals"]["strength"] == {
        "s1": 300,
        "top20": 300,
        "top100": 300,
    }
    assert policies["regular"]["intervals"]["large_trade"]["top100"] == 0


def test_patch_has_no_new_thread_qax_or_price_callback():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "Thread(",
        "QAxWidget",
        "CommConnect",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "subprocess",
        "Start-Process",
        "document.",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source
    assert "large_trade_enabled=False" in collector_source


def test_source_no_longer_hard_caps_rest_scope_at_twenty():
    source = PATCH_PATH.read_text(encoding="utf-8")

    assert "min(100, scope)" in source
    assert 'return "top20" if position < 20 else "top100"' in source
    assert "overdue * 1000.0" in source
