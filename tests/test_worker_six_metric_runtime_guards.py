from __future__ import annotations

import ast
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle
import realtime_v2.worker_six_metric_lifecycle_runtime_opt as runtime_opt
import realtime_v2.worker_six_metric_output_guard as output_guard

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_OPT_PATH = ROOT / "realtime_v2" / "worker_six_metric_lifecycle_runtime_opt.py"
OUTPUT_GUARD_PATH = ROOT / "realtime_v2" / "worker_six_metric_output_guard.py"


def test_runtime_opt_calculates_premarket_boundary_once_per_minute(monkeypatch):
    calls = {"count": 0}

    def original_boundary(now=None):
        calls["count"] += 1
        return datetime(2026, 7, 17, 8, 0, 0)

    def original_entry(code, group, values, *, source_date, now):
        return {
            "stock_code": code,
            "group": group,
            "source_trading_date": source_date,
            "captured_at": "volatile",
            "expires_at": "2026-07-17T08:00:00",
            "values": dict(values),
        }

    monkeypatch.setattr(lifecycle, "_next_premarket_boundary", original_boundary)
    monkeypatch.setattr(lifecycle, "_entry_from_values", original_entry)
    monkeypatch.setattr(
        lifecycle,
        "_stockboard_six_metric_runtime_opt_installed",
        False,
        raising=False,
    )

    runtime_opt.install()
    now = datetime(2026, 7, 16, 10, 15, 30)

    assert lifecycle._next_premarket_boundary(now) == datetime(2026, 7, 17, 8, 0, 0)
    assert lifecycle._next_premarket_boundary(now) == datetime(2026, 7, 17, 8, 0, 0)
    assert calls["count"] == 1

    values = {"orderbook_received_at": "2026-07-16T10:15:20+09:00"}
    first = lifecycle._entry_from_values(
        "000660",
        "orderbook",
        values,
        source_date="20260716",
        now=now,
    )
    second = lifecycle._entry_from_values(
        "000660",
        "orderbook",
        values,
        source_date="20260716",
        now=now,
    )
    assert first["captured_at"] == "2026-07-16T10:15:20+09:00"
    assert first == second


def test_output_guard_removes_wrong_sources_and_keeps_valid_zero_program(monkeypatch):
    monkeypatch.setattr(
        output_guard,
        "market_session_now",
        lambda now=None: SimpleNamespace(
            phase="regular",
            trading_date="20260716",
            calendar_date="20260716",
        ),
    )

    class FakeState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}

        def rows(self, limit=300):
            return [
                {
                    "stock_code": "000660",
                    "amount_ratio": 0,
                    "received_at": "2026-07-16T10:00:00+09:00",
                    "bid_ask_ratio": 1.5,
                    "orderbook_received_at": "2026-07-15T19:00:00+09:00",
                    "orderbook_source": "ka10004_rest_lowload",
                    "execution_strength": 98.68,
                    "execution_strength_source": "ka10046_rest_lowload",
                    "execution_strength_received_at": "2026-07-16T10:00:01+09:00",
                    "strength_5m": 101.2,
                    "strength_snapshot_at": "2026-07-15T15:30:00+09:00",
                    "strength_source": "ka10046_rest_lowload",
                    "program_net": 0.0,
                    "program_net_updated_at": "2026-07-16T10:00:02+09:00",
                    "program_net_source": "ka90004_tr_singleflight",
                    "program_net_status": "ok",
                }
            ][:limit]

    class FakeBase:
        State = FakeState

    output_guard.install(FakeBase)
    state = FakeState()
    row = state.rows()[0]

    assert "amount_ratio" not in row
    assert "bid_ask_ratio" not in row
    assert "execution_strength" not in row
    assert "strength_5m" not in row
    assert row["program_net"] == 0.0
    assert row["program_available"] is True
    assert state.status["six_metric_output_execution_removed_count"] == 1
    assert state.status["six_metric_output_program_accepted_count"] == 1


def test_runtime_guard_files_add_no_new_data_source_or_thread():
    for path in (RUNTIME_OPT_PATH, OUTPUT_GUARD_PATH):
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        for forbidden in (
            "Thread(",
            "requests.",
            "urlopen(",
            "QAxWidget",
            "SetRealReg",
            "GetCommRealData",
            "dynamicCall",
            "document.",
        ):
            assert forbidden not in source
