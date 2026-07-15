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


def test_regular_session_keeps_integrated_suffix():
    install()
    updater = _updater_at(10 * 60)

    assert updater._session_phase() == "regular"
    assert updater._in_regular_session() is True
    assert updater._query_code("000660") == "000660_AL"
    assert updater._interval("bidask", "s1") == 10
    assert updater.state.status["rest_live_metrics_query_suffix"] == "_AL"


def test_aftermarket_uses_nxt_suffix_and_active_intervals():
    install()
    updater = _updater_at(16 * 60 + 10)

    assert updater._session_phase() == "aftermarket"
    assert updater._in_regular_session() is True
    assert updater._query_code("000660") == "000660_NX"
    assert updater._interval("bidask", "s1") == 10
    assert updater._interval("bidask", "top20") == 60
    assert updater._interval("strength", "s1") == 30
    assert updater._interval("strength", "top20") == 180
    assert updater._interval("large_trade", "s1") == 120
    assert updater._interval("large_trade", "top20") == 600
    assert updater.state.status["rest_live_metrics_query_suffix"] == "_NX"
    assert updater.state.status["rest_live_metrics_market_phase"] == "aftermarket"


def test_outside_active_sessions_stays_disabled():
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
    assert config["query_suffix_by_session"] == {
        "regular": "_AL",
        "aftermarket": "_NX",
    }
    assert config["aftermarket_session"] == {
        "start": "15:30",
        "end": "20:00",
    }
