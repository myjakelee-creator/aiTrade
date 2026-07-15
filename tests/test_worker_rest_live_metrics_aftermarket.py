from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

from realtime_v2.worker_rest_live_metrics_aftermarket_patch import install
from realtime_v2.worker_rest_live_metrics_patch import CONFIG_PATH, RestLiveMetricUpdater

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_rest_live_metrics_aftermarket_patch.py"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {}


def _updater_at(minute: int) -> RestLiveMetricUpdater:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    updater = RestLiveMetricUpdater(FakeState(), config=config)
    updater._minute_now = lambda: minute
    return updater


def test_regular_and_aftermarket_keep_integrated_suffix():
    install()
    regular = _updater_at(10 * 60)
    aftermarket = _updater_at(16 * 60 + 10)

    assert regular._session_phase() == "regular"
    assert regular._in_regular_session() is True
    assert regular._query_code("000660") == "000660_AL"
    assert regular.state.status["rest_live_metrics_query_suffix"] == "_AL"

    assert aftermarket._session_phase() == "aftermarket"
    assert aftermarket._in_regular_session() is True
    assert aftermarket._query_code("000660") == "000660_AL"
    assert aftermarket.state.status["rest_live_metrics_query_suffix"] == "_AL"
    assert aftermarket.state.status["rest_live_metrics_market_phase"] == "aftermarket"


def test_authoritative_session_manager_owns_aftermarket_intervals_and_top100_scope():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    policy = config["session_manager"]["phase_policies"]["aftermarket"]

    assert policy["scope"] == 100
    assert policy["intervals"]["bidask"] == {
        "s1": 10,
        "top20": 60,
        "top100": 600,
    }
    assert policy["intervals"]["strength"] == {
        "s1": 30,
        "top20": 180,
        "top100": 900,
    }
    assert policy["intervals"]["large_trade"] == {
        "s1": 120,
        "top20": 600,
        "top100": 1800,
    }


def test_legacy_aftermarket_wrapper_still_pauses_after_twenty_hundred():
    install()
    updater = _updater_at(20 * 60 + 1)

    assert updater._session_phase() == "outside"
    assert updater._in_regular_session() is False


def test_aftermarket_patch_does_not_touch_qax_or_price_callback():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "QAxWidget",
        "CommConnect",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "subprocess",
        "Start-Process",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert config["rollout_stage"] == 4
    for phase in (
        "before_market",
        "premarket",
        "opening_call",
        "opening_burst",
        "regular",
        "closing_call",
        "after_wait",
        "aftermarket",
        "closed",
    ):
        assert config["query_suffix_by_session"][phase] == "_AL"
    assert config["aftermarket_session"] == {
        "start": "15:30",
        "end": "20:00",
    }
