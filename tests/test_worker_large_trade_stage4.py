from __future__ import annotations

import ast
import threading
from pathlib import Path

import realtime_v2.worker_large_trade_stage4_patch as stage4_module
from realtime_v2.worker_large_trade_stage4_patch import (
    SOURCE,
    STATUS,
    _aggregate,
    install,
)

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_large_trade_stage4_patch.py"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


class FakeState:
    initial_daily = {}

    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {}
        self.daily_values_by_code = {
            code: dict(values) for code, values in self.initial_daily.items()
        }
        self.rebuild_reasons = []
        self.daily_dirty = False

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def request_background_rebuild(self, *, reason, force=False):
        self.rebuild_reasons.append((reason, force))

    def apply_rest_large_trade_delta(
        self,
        code,
        *,
        buy_count,
        sell_count,
        buy_sum_eok,
        sell_sum_eok,
        threshold_krw,
        updated_at,
    ):
        quote = self._quote(code)
        quote["large_trade_buy_count"] = int(quote.get("large_trade_buy_count") or 0) + buy_count
        quote["large_trade_sell_count"] = int(quote.get("large_trade_sell_count") or 0) + sell_count
        quote["large_trade_buy_sum_eok"] = round(
            float(quote.get("large_trade_buy_sum_eok") or 0) + buy_sum_eok,
            4,
        )
        quote["large_trade_sell_sum_eok"] = round(
            float(quote.get("large_trade_sell_sum_eok") or 0) + sell_sum_eok,
            4,
        )
        quote["large_trade_net_count"] = (
            quote["large_trade_buy_count"] - quote["large_trade_sell_count"]
        )
        quote["large_trade_net_sum_eok"] = round(
            quote["large_trade_buy_sum_eok"] - quote["large_trade_sell_sum_eok"],
            4,
        )
        quote["large_trade_threshold_krw"] = threshold_krw
        quote["large_trade_updated_at"] = updated_at
        self.daily_values_by_code[code] = dict(quote)
        return 1


class FakeUpdater:
    def __init__(self, state):
        self.state = state
        self.large_seen = {}
        self.status_values = {}

    def _metric_config(self, metric):
        assert metric == "large_trade"
        return {"threshold_krw": 50_000_000}

    def _apply(self, code, metric, payload):
        return metric != "large_trade"

    def _status(self, **values):
        self.status_values.update(values)


class FakeBase:
    State = FakeState
    DAILY_PERSIST_KEYS = ()


def _payload(*rows):
    return {"tdy_pred_cntr_qty": list(rows)}


def _row(time_text, price, qty, cumulative):
    return {
        "cntr_tm": time_text,
        "cntr_pric": str(price),
        "cntr_qty": str(qty),
        "acc_trde_qty": str(cumulative),
    }


def _install(monkeypatch):
    import realtime_v2.worker_rest_live_metrics_patch as rest_module

    monkeypatch.setattr(stage4_module, "trading_date_text", lambda: "20260716")
    monkeypatch.setattr(rest_module, "RestLiveMetricUpdater", FakeUpdater)
    install(FakeBase)


def test_aggregate_separates_signed_buy_and_sell_large_trades():
    rows = [
        {"qty": 500, "amount_krw": 50_000_000, "is_large": True},
        {"qty": -250, "amount_krw": 75_000_000, "is_large": True},
        {"qty": 10, "amount_krw": 1_000_000, "is_large": False},
    ]

    assert _aggregate(rows) == {
        "buy_count": 1,
        "sell_count": 1,
        "buy_sum_eok": 0.5,
        "sell_sum_eok": 0.75,
    }


def test_first_page_initializes_when_current_day_snapshot_is_missing(monkeypatch):
    FakeState.initial_daily = {}
    _install(monkeypatch)
    state = FakeState()
    updater = FakeUpdater(state)

    applied = updater._apply(
        "000660",
        "large_trade",
        _payload(
            _row("090001", 100_000, 500, 500),
            _row("090002", 200_000, -250, 750),
        ),
    )

    assert applied is True
    quote = state.quotes["000660"]
    assert quote["large_trade_buy_count"] == 1
    assert quote["large_trade_sell_count"] == 1
    assert quote["large_trade_net_count"] == 0
    assert quote["large_trade_source"] == SOURCE
    assert quote["large_trade_status"] == STATUS
    assert updater.status_values["large_trade_stage4_first_mode"] == (
        "initialize_from_first_page"
    )


def test_first_page_resumes_existing_daily_without_duplicate(monkeypatch):
    FakeState.initial_daily = {
        "000660": {
            "large_trade_buy_count": 3,
            "large_trade_sell_count": 1,
            "large_trade_net_count": 2,
            "large_trade_source": SOURCE,
            "large_trade_status": STATUS,
            "large_trade_updated_at": "2026-07-16T10:00:00+09:00",
            "large_trade_trading_date": "20260716",
        }
    }
    _install(monkeypatch)
    state = FakeState()
    updater = FakeUpdater(state)

    updater._apply(
        "000660",
        "large_trade",
        _payload(_row("100000", 100_000, 500, 500)),
    )

    assert "000660" not in state.quotes
    assert state.daily_values_by_code["000660"]["large_trade_net_count"] == 2
    assert updater.status_values["large_trade_stage4_first_mode"] == (
        "resume_existing_daily"
    )


def test_second_page_adds_only_new_rows(monkeypatch):
    FakeState.initial_daily = {}
    _install(monkeypatch)
    state = FakeState()
    updater = FakeUpdater(state)
    first = _row("090001", 100_000, 500, 500)
    second = _row("090002", 200_000, -250, 750)

    updater._apply("000660", "large_trade", _payload(first))
    updater._apply("000660", "large_trade", _payload(second, first))

    quote = state.quotes["000660"]
    assert quote["large_trade_buy_count"] == 1
    assert quote["large_trade_sell_count"] == 1
    assert quote["large_trade_net_count"] == 0
    assert quote["large_trade_source"] == SOURCE
    assert updater.status_values["large_trade_stage4_last_new_rows"] == 1


def test_stage4_patch_adds_no_qax_thread_or_realtime_fid():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "Thread(",
        "QAxWidget",
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
