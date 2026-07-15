from __future__ import annotations

import ast
import threading
from copy import deepcopy
from pathlib import Path

from realtime_v2.common import trading_date_text
from realtime_v2.worker_metric_restore_patch import (
    LARGE_TRADE_NUMERIC_KEYS,
    install,
)

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_metric_restore_patch.py"
SINGLEFLIGHT_PATH = ROOT / "realtime_v2" / "worker_tr_singleflight_patch.py"


def _today_at(clock: str = "10:00:00") -> str:
    date = str(trading_date_text())
    return f"{date[:4]}-{date[4:6]}-{date[6:8]}T{clock}"


class FakeState:
    def __init__(
        self,
        rows=None,
        daily=None,
        session_hold=None,
        continuity=None,
    ):
        self.lock = threading.RLock()
        self.status = {}
        self.daily_values_by_code = dict(daily or {})
        self.session_metric_hold_by_code = dict(session_hold or {})
        self.board_metric_continuity_by_code = dict(continuity or {})
        self._rows = list(rows or [])
        self.rebuild_requests = []

    def apply_program_net_values(self, values, source, status):
        updated = 0
        for code, value in (values or {}).items():
            entry = self.daily_values_by_code.setdefault(code, {})
            entry.update(
                {
                    "program_net": value,
                    "program_net_updated_at": _today_at("09:01:00"),
                    "program_net_source": source,
                    "program_net_status": status,
                }
            )
            updated += 1
        return updated

    def rows(self, limit=300):
        return deepcopy(self._rows[:limit])

    def request_background_rebuild(self, **kwargs):
        self.rebuild_requests.append(dict(kwargs))


class FakeUpdater:
    def __init__(self, state):
        self.state = state
        self.interval_sec = 60.0
        self.stop_event = threading.Event()
        self.calls = []

    def _load_existing_snapshots(self):
        self.calls.append("load")

    def _fetch_once(self):
        self.calls.append("fetch")
        self.stop_event.set()


class FakeBase:
    State = FakeState
    ProgramNetUpdater = FakeUpdater


def install_once():
    install(FakeBase)


def test_program_fetch_runs_at_startup_on_existing_updater_thread(monkeypatch):
    install_once()
    monkeypatch.setenv("STOCKBOARD_PROGRAM_STARTUP_REFRESH_DELAY_SEC", "0")
    state = FakeState()
    updater = FakeUpdater(state)

    updater.run()

    assert updater.calls == ["load", "fetch"]
    assert state.status["program_net_startup_refresh_count"] == 1
    assert state.status["program_net_startup_refresh_status"] == "ok"


def test_program_update_requests_one_background_snapshot_rebuild():
    install_once()
    state = FakeState()

    updated = state.apply_program_net_values(
        {"000001": 12.0}, "ka90004_tr_singleflight", "ok"
    )

    assert updated == 1
    assert state.rebuild_requests == [
        {"reason": "program_net_update", "force": True}
    ]


def test_same_day_program_and_large_trade_values_are_restored():
    install_once()
    state = FakeState(
        rows=[
            {
                "stock_code": "000001",
                "program_net": 0,
                "program_net_source": "new_session_wait",
                "large_trade_buy_count": 0,
                "large_trade_sell_count": 0,
                "large_trade_net_count": 0,
                "large_trade_buy_sum_eok": 0.0,
                "large_trade_sell_sum_eok": 0.0,
                "large_trade_net_sum_eok": 0.0,
                "large_trade_source": "new_session_reset",
            }
        ],
        daily={
            "000001": {
                "program_net": 23.0,
                "program_net_updated_at": _today_at(),
                "program_net_source": "ka90004_tr_singleflight",
                "program_net_status": "ok",
                "large_trade_buy_count": 12,
                "large_trade_sell_count": 4,
                "large_trade_net_count": 8,
                "large_trade_buy_sum_eok": 18.0,
                "large_trade_sell_sum_eok": 5.0,
                "large_trade_net_sum_eok": 13.0,
                "large_trade_source": "collector_aggregate",
                "large_trade_status": "cached_current_session",
                "large_trade_updated_at": _today_at(),
            }
        },
    )

    row = state.rows()[0]

    assert row["program_net"] == 23.0
    assert row["program_net_available"] is True
    assert row["large_trade_net_count"] == 8
    assert row["large_trade_available"] is True
    assert row["large_trade_cache_restored"] is True


def test_current_session_hold_and_continuity_caches_are_valid_sources():
    install_once()
    date = str(trading_date_text())
    state = FakeState(
        rows=[
            {
                "stock_code": "000010",
                "program_net": 0,
                "program_net_source": "new_session_wait",
                "large_trade_net_count": 0,
                "large_trade_source": "new_session_reset",
            }
        ],
        session_hold={
            "000010": {
                "program_net": -31.0,
                "program_net_source": "ka90004_tr_singleflight",
                "program_net_status": "ok",
                "_session_hold_program_date": date,
            }
        },
        continuity={
            "000010": {
                "large_trade_buy_count": 9,
                "large_trade_sell_count": 3,
                "large_trade_net_count": 6,
                "large_trade_buy_sum_eok": 12.0,
                "large_trade_sell_sum_eok": 4.0,
                "large_trade_net_sum_eok": 8.0,
                "large_trade_source": "collector_aggregate",
                "large_trade_status": "ok",
                "_metric_continuity_large_trade_date": date,
            }
        },
    )

    row = state.rows()[0]

    assert row["program_net"] == -31.0
    assert row["large_trade_net_count"] == 6
    assert row["program_net_display_basis"] == "same_day_cache"
    assert row["large_trade_display_basis"] == "same_day_cache"


def test_previous_session_cache_is_not_restored_during_current_day():
    install_once()
    state = FakeState(
        rows=[
            {
                "stock_code": "000011",
                "program_net": 0,
                "program_net_source": "new_session_wait",
                "large_trade_net_count": 0,
                "large_trade_source": "new_session_reset",
            }
        ],
        session_hold={
            "000011": {
                "program_net": 99.0,
                "program_net_source": "ka90004_tr_singleflight",
                "program_net_status": "ok",
                "_session_hold_program_date": "20200101",
            }
        },
        continuity={
            "000011": {
                "large_trade_net_count": 77,
                "large_trade_source": "collector_aggregate",
                "large_trade_status": "ok",
                "_metric_continuity_large_trade_date": "20200101",
            }
        },
    )

    row = state.rows()[0]

    assert "program_net" not in row
    assert "large_trade_net_count" not in row
    assert row["program_net_available"] is False
    assert row["large_trade_available"] is False


def test_unavailable_metrics_omit_false_zero_display_values():
    install_once()
    state = FakeState(
        rows=[
            {
                "stock_code": "000002",
                "program_net": 0,
                "program_net_source": "new_session_wait",
                "large_trade_buy_count": 0,
                "large_trade_sell_count": 0,
                "large_trade_net_count": 0,
                "large_trade_buy_sum_eok": 0.0,
                "large_trade_sell_sum_eok": 0.0,
                "large_trade_net_sum_eok": 0.0,
                "large_trade_source": "new_session_reset",
            }
        ]
    )

    row = state.rows()[0]

    assert "program_net" not in row
    assert row["program_net_available"] is False
    assert all(key not in row for key in LARGE_TRADE_NUMERIC_KEYS)
    assert row["large_trade_available"] is False
    assert row["large_trade_status"] == "unavailable_no_current_source"


def test_genuine_zero_with_valid_source_is_not_hidden():
    install_once()
    state = FakeState(
        rows=[
            {
                "stock_code": "000003",
                "program_net": 0,
                "program_net_source": "ka90004_tr_singleflight",
                "program_net_status": "ok",
                "program_net_updated_at": _today_at(),
                "large_trade_buy_count": 2,
                "large_trade_sell_count": 2,
                "large_trade_net_count": 0,
                "large_trade_buy_sum_eok": 3.0,
                "large_trade_sell_sum_eok": 3.0,
                "large_trade_net_sum_eok": 0.0,
                "large_trade_source": "collector_aggregate",
                "large_trade_status": "ok",
                "large_trade_updated_at": _today_at(),
            }
        ]
    )

    row = state.rows()[0]

    assert row["program_net"] == 0
    assert row["program_net_available"] is True
    assert row["large_trade_net_count"] == 0
    assert row["large_trade_available"] is True


def test_patch_adds_no_price_collector_or_parallel_market_data_work():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "dynamicCall",
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "CommRqData",
        "requests.",
        "socket.",
        "Thread(",
        "EventSource(",
        "fetch(",
    ):
        assert forbidden not in source

    singleflight_source = SINGLEFLIGHT_PATH.read_text(encoding="utf-8")
    assert "install_metric_restore(base)" in singleflight_source
