from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

from realtime_v2.worker_rest_live_metrics_patch import (
    CONFIG_PATH,
    RestLiveMetricUpdater,
    install,
    parse_bidask_payload,
    parse_large_trade_page,
    parse_strength_payload,
)

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_rest_live_metrics_patch.py"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


def _config(stage: int) -> dict:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    payload["rollout_stage"] = stage
    payload["health_stable_sec"] = 0
    return payload


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "trade_count": 10,
            "collector_status": {
                "status": {
                    "running": True,
                    "login_state": "connected",
                    "realreg_succeeded": True,
                    "realreg_code_count": 100,
                    "last_error": None,
                }
            },
        }
        self.quotes = {
            "000001": {"stock_code": "000001", "trade_value_eok": 1000},
            "000002": {"stock_code": "000002", "trade_value_eok": 900},
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


class FakeProgramUpdater:
    def __init__(self, state, interval_sec=60):
        self.state = state
        self.interval_sec = interval_sec
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeBase:
    State = FakeState
    ProgramNetUpdater = FakeProgramUpdater
    DAILY_PERSIST_KEYS = ()


def test_bidask_parser_uses_total_bid_and_ask_volume():
    values = parse_bidask_payload(
        {
            "tot_sel_req": "3000",
            "tot_buy_req": "6000",
            "sel_fpr_bid": "101000",
            "buy_fpr_bid": "100900",
        },
        updated_at="2026-07-15T15:00:00+09:00",
    )

    assert values["bid_ask_ratio"] == 2.0
    assert values["bid_pct"] == 66.6667
    assert values["ask_pct"] == 33.3333
    assert values["bid_volume"] == 6000
    assert values["ask_volume"] == 3000
    assert values["orderbook_source"] == "ka10004_rest_lowload"


def test_strength_parser_applies_five_minute_only_from_stage_three():
    payload = {
        "cntr_str_tm": [
            {
                "cntr_tm": "150301",
                "cntr_str": "118.25",
                "cntr_str_5min": "112.50",
                "cntr_str_20min": "108.10",
                "cntr_str_60min": "104.00",
            }
        ]
    }

    stage_two = parse_strength_payload(payload, apply_five_minute=False)
    stage_three = parse_strength_payload(payload, apply_five_minute=True)

    assert stage_two["execution_strength"] == 118.25
    assert stage_two["execution_strength_source"] == "ka10046_rest_lowload"
    assert "strength_5m" not in stage_two
    assert stage_three["execution_strength"] == 118.25
    assert stage_three["strength_5m"] == 112.5
    assert stage_three["strength_20m"] == 108.1
    assert stage_three["strength_60m"] == 104.0


def test_large_trade_parser_preserves_signed_quantity_and_threshold():
    rows = parse_large_trade_page(
        {
            "tdy_pred_cntr_qty": [
                {
                    "cntr_tm": "150300",
                    "cntr_pric": "100000",
                    "cntr_qty": "+500",
                    "acc_trde_qty": "10000",
                },
                {
                    "cntr_tm": "150301",
                    "cntr_pric": "200000",
                    "cntr_qty": "-250",
                    "acc_trde_qty": "10250",
                },
                {
                    "cntr_tm": "150302",
                    "cntr_pric": "100000",
                    "cntr_qty": "+499",
                    "acc_trde_qty": "10749",
                },
            ]
        },
        threshold_krw=50_000_000,
    )

    assert rows[0]["qty"] == 500
    assert rows[0]["is_large"] is True
    assert rows[1]["qty"] == -250
    assert rows[1]["is_large"] is True
    assert rows[2]["is_large"] is False


def test_rollout_stage_one_requests_only_bidask():
    updater = RestLiveMetricUpdater(FakeState(), config=_config(1))

    assert updater._metric_enabled("bidask") is True
    assert updater._metric_enabled("strength") is False
    assert updater._metric_enabled("large_trade") is False


def test_rollout_stage_two_adds_strength_but_not_five_minute_or_large_trade():
    updater = RestLiveMetricUpdater(FakeState(), config=_config(2))

    assert updater._metric_enabled("bidask") is True
    assert updater._metric_enabled("strength") is True
    assert updater._metric_enabled("large_trade") is False

    values = parse_strength_payload(
        {
            "cntr_str_tm": [
                {
                    "cntr_tm": "160500",
                    "cntr_str": "121.5",
                    "cntr_str_5min": "110.2",
                }
            ]
        },
        apply_five_minute=updater.stage >= 3,
    )
    assert values["execution_strength"] == 121.5
    assert "strength_5m" not in values


def test_install_applies_values_and_requests_one_coalesced_rebuild():
    class LocalState(FakeState):
        pass

    class LocalProgramUpdater(FakeProgramUpdater):
        pass

    class LocalBase:
        State = LocalState
        ProgramNetUpdater = LocalProgramUpdater
        DAILY_PERSIST_KEYS = ()

    install(LocalBase)
    state = LocalState()

    updated = state.apply_rest_live_metric_values(
        "000001",
        {
            "bid_ask_ratio": 1.5,
            "bid_volume": 600,
            "ask_volume": 400,
            "orderbook_received_at": "2026-07-15T15:03:00+09:00",
            "orderbook_source": "ka10004_rest_lowload",
            "orderbook_status": "ok",
        },
        "bidask",
    )

    assert updated == 1
    assert state.quotes["000001"]["bid_ask_ratio"] == 1.5
    assert state.quotes["000001"]["orderbook_source"] == "ka10004_rest_lowload"
    assert state.daily_values_by_code["000001"]["bid_ask_ratio"] == 1.5
    assert state.daily_values_by_code["000001"]["orderbook_status"] == "ok"
    assert state.rebuild_reasons == [("rest_live_metric:bidask", False)]


def test_large_trade_first_page_is_baseline_then_only_new_rows_increment():
    class LocalState(FakeState):
        pass

    class LocalProgramUpdater(FakeProgramUpdater):
        pass

    class LocalBase:
        State = LocalState
        ProgramNetUpdater = LocalProgramUpdater
        DAILY_PERSIST_KEYS = ()

    install(LocalBase)
    state = LocalState()
    updater = RestLiveMetricUpdater(state, config=_config(4))

    first = {
        "tdy_pred_cntr_qty": [
            {
                "cntr_tm": "150300",
                "cntr_pric": "100000",
                "cntr_qty": "+500",
                "acc_trde_qty": "10000",
            }
        ]
    }
    second = {
        "tdy_pred_cntr_qty": [
            {
                "cntr_tm": "150301",
                "cntr_pric": "200000",
                "cntr_qty": "-250",
                "acc_trde_qty": "10250",
            },
            *first["tdy_pred_cntr_qty"],
        ]
    }

    assert updater._apply("000001", "large_trade", first) is True
    assert state.quotes["000001"].get("large_trade_net_count") is None

    assert updater._apply("000001", "large_trade", second) is True
    assert state.quotes["000001"]["large_trade_buy_count"] == 0
    assert state.quotes["000001"]["large_trade_sell_count"] == 1
    assert state.quotes["000001"]["large_trade_net_count"] == -1
    assert state.quotes["000001"]["large_trade_source"] == "ka10055_rest_incremental"


def test_patch_does_not_add_qax_realtime_or_browser_calculation():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "QAxWidget",
        "CommConnect",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "EventSource(",
        "document.querySelector",
        "window.open",
        "subprocess",
        "Start-Process",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source
    assert "large_trade_enabled=False" in collector_source

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    contract = config["performance_contract"]
    assert contract["price_collector_changes"] == 0
    assert contract["new_qax_processes"] == 0
    assert contract["new_realtime_fids"] == 0
    assert contract["browser_calculation"] is False
    assert config["rollout_stage"] == 2
