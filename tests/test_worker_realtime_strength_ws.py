from __future__ import annotations

import ast
import json
import threading
from copy import deepcopy
from pathlib import Path

from realtime_v2.worker_realtime_strength_ws_patch import (
    WS_SOURCE,
    install,
    parse_realtime_strength_message,
    resolve_s1_code,
)

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_realtime_strength_ws_patch.py"
CONFIG_FILE = ROOT / "configs" / "stockboard_live_metrics_rest.json"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "rank": 1,
                "trade_value_eok": 1000,
                "execution_strength": 98.68,
                "execution_strength_source": "ka10046_rest_lowload",
            },
            "010950": {
                "stock_code": "010950",
                "rank": 50,
                "trade_value_eok": 100,
                "execution_strength": 88.0,
                "execution_strength_source": "ka10046_rest_lowload",
            },
        }
        self.daily_values_by_code = {}
        self.rebuild_reasons = []
        self.daily_dirty = False

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def request_background_rebuild(self, *, reason, force=False):
        self.rebuild_reasons.append((reason, force))

    def rows(self, limit=300):
        return [deepcopy(row) for row in self.quotes.values()][:limit]


class FakeProgramUpdater:
    def __init__(self, state, *args, **kwargs):
        self.state = state

    def start(self):
        return None

    def stop(self):
        return None


class FakeBase:
    State = FakeState
    ProgramNetUpdater = FakeProgramUpdater


def test_parse_realtime_0b_fid228_message():
    events = parse_realtime_strength_message(
        {
            "trnm": "REAL",
            "data": [
                {
                    "type": "0B",
                    "name": "주식체결",
                    "item": "000660_AL",
                    "values": {
                        "20": "181501",
                        "10": "+2067000",
                        "228": "95.04",
                        "290": "3",
                        "9081": "2",
                    },
                }
            ],
        }
    )

    assert events == [
        {
            "stock_code": "000660",
            "execution_strength": 95.04,
            "execution_strength_source_time": "181501",
            "execution_strength_exchange": "2",
            "execution_strength_market_phase": "3",
            "execution_strength_trade_price": 2067000.0,
            "raw_item": "000660_AL",
        }
    ]


def test_s1_resolution_uses_canonical_rank1_not_stale_runtime_value():
    code, source = resolve_s1_code(FakeState())

    assert code == "000660"
    assert source == "canonical_rank1"


def test_install_applies_fid228_and_hides_legacy_ka10046_snapshot():
    class LocalState(FakeState):
        pass

    class LocalProgramUpdater(FakeProgramUpdater):
        pass

    class LocalBase:
        State = LocalState
        ProgramNetUpdater = LocalProgramUpdater

    install(LocalBase)
    state = LocalState()
    state.status["realtime_strength_ws_selected_code"] = "000660"

    changed = state.apply_realtime_strength_ws(
        {
            "stock_code": "000660",
            "execution_strength": 95.04,
            "execution_strength_source_time": "181501",
            "execution_strength_exchange": "2",
            "execution_strength_market_phase": "3",
            "execution_strength_trade_price": 2067000,
        }
    )

    assert changed is True
    assert state.quotes["000660"]["execution_strength"] == 95.04
    assert state.quotes["000660"]["execution_strength_source"] == WS_SOURCE
    assert state.rebuild_reasons == [("realtime_strength_ws", False)]

    rows = {row["stock_code"]: row for row in state.rows()}
    assert rows["000660"]["execution_strength"] == 95.04
    assert rows["000660"]["execution_strength_available"] is True
    assert "execution_strength" not in rows["010950"]
    assert rows["010950"]["execution_strength_legacy_snapshot"] == 88.0


def test_stage4_uses_one_top100_websocket_and_batch_apply():
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))

    assert config["rollout_stage"] == 4
    assert config["metrics"]["strength"]["stage"] == 3
    assert config["metrics"]["large_trade"]["stage"] == 4
    assert config["realtime_strength_ws"]["stage"] == 2
    assert config["realtime_strength_ws"]["scope"] == "top100"
    assert config["realtime_strength_ws"]["max_symbols"] == 100
    assert config["realtime_strength_ws"]["type"] == "0B"
    assert config["realtime_strength_ws"]["strength_field"] == "228"
    contract = config["performance_contract"]
    assert contract["realtime_strength_ws_connections"] == 1
    assert contract["realtime_strength_ws_symbols"] == 100
    assert contract["realtime_strength_batch_apply_hz_max"] == 1


def test_websocket_patch_does_not_modify_qax_price_collector():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "QAxWidget",
        "CommConnect",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "Start-Process",
        "subprocess",
        "document.",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source
    assert "large_trade_enabled=False" in collector_source
