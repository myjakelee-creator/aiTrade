from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.after_close_recovery import (
    AfterCloseRecoveryCoordinator,
    CloseWindowSamplerService,
    merge_sampler_theme,
    minute_rows_to_recovery,
    prefer_source_value,
    provider_tr_idle,
    source_value,
)


def test_exact_integrated_value_is_not_replaced_by_krx_estimate():
    existing = source_value(
        100.0,
        "CLOSE_SAMPLER_EXACT",
        "EXACT",
        "2026-07-10T20:00:00",
        "20260710",
        "AL",
        1.0,
        False,
    )
    candidate = source_value(
        90.0,
        "MINUTE_CLOSE_X_VOLUME",
        "ESTIMATED",
        "2026-07-10T20:01:00",
        "20260710",
        "KRX",
        0.65,
        True,
    )
    assert prefer_source_value(existing, candidate) == existing


def test_newer_trading_date_requires_explicit_rollover():
    existing = source_value(
        100.0,
        "PERSISTED_LAST_VALID",
        "HELD",
        "2026-07-10T20:00:00",
        "20260710",
        "AL",
        0.8,
        False,
    )
    candidate = source_value(
        110.0,
        "LIVE_REALTIME",
        "LIVE",
        "2026-07-13T08:01:00",
        "20260713",
        "AL",
        1.0,
        False,
    )
    assert prefer_source_value(existing, candidate) == existing
    assert prefer_source_value(existing, candidate, allow_rollover=True) == candidate


def test_minute_fallback_sums_each_minute_close_times_volume():
    rows = [
        {
            "close": 1000,
            "volume": 10000,
            "time": "20260710200000",
            "open": 990,
            "high": 1010,
            "low": 980,
        },
        {
            "close": 900,
            "volume": 20000,
            "time": "20260710195900",
            "open": 880,
            "high": 910,
            "low": 870,
        },
    ]
    result = minute_rows_to_recovery(
        rows,
        "20260710",
        "AL",
        "2026-07-10T20:01:00",
    )
    assert result["minute_recovery_status"] == "ok"
    assert result["minute_trade_value_1m_eok"] == 0.1
    assert result["minute_trade_value_5m_eok"] == 0.28
    assert result["minute_ohlc"] == {
        "open": 880,
        "high": 1010,
        "low": 870,
        "close": 1000,
    }
    metadata = result["recovery_values"]["trade_value_5m_eok"]
    assert metadata["source"] == "MINUTE_CLOSE_X_VOLUME"
    assert metadata["is_estimated"] is True
    assert metadata["coverage"] == 0.4


def test_provider_arbitration_blocks_any_competing_lane():
    provider = SimpleNamespace(
        _lock=threading.RLock(),
        _running=True,
        _login_state="connected",
        _strength_probe_inflight=None,
        _orderbook_probe_inflight=None,
        _opt10055_probe_inflight=None,
        _minute_recovery_inflight=None,
        _strength_probe_pending=[],
        _orderbook_probe_pending=[],
        _opt10055_probe_pending=[],
        _close_metrics_queue=[],
        _minute_recovery_pending=[],
        _strength_probe_last_request_at=0.0,
        _orderbook_probe_last_request_at=0.0,
        _opt10055_probe_last_request_at=0.0,
        _close_metrics_last_request_at=0.0,
        _minute_recovery_last_request_at=0.0,
    )
    assert provider_tr_idle(provider)[0] is True
    provider._minute_recovery_inflight = {"stock_code": "005930"}
    ready, reason = provider_tr_idle(provider)
    assert ready is False
    assert reason == "_minute_recovery_inflight"
    provider._minute_recovery_inflight = None
    provider._strength_probe_pending.append({"stock_code": "000660"})
    ready, reason = provider_tr_idle(provider)
    assert ready is False
    assert reason == "_strength_probe_pending"


def test_sampler_persists_exact_weighted_theme_deltas(tmp_path):
    state = SimpleNamespace(lock=threading.RLock(), quotes={}, status={})
    service = CloseWindowSamplerService.__new__(CloseWindowSamplerService)
    threading.Thread.__init__(service, daemon=True)
    service.state = state
    service.persist_path = tmp_path / "close_flow_sampler_last.json"
    service.theme_members = {
        "THEME": (("000001", 0.6), ("000002", 0.4)),
    }
    service.codes = {"000001", "000002"}
    service.samples = []
    service.persist_count = 0
    service.last_saved_at = None
    service.last_error = None

    end = datetime(2026, 7, 10, 20, 0, 10)
    service.samples = [
        (
            end - timedelta(seconds=300),
            {"000001": 100.0, "000002": 50.0},
            {"000001": "AL", "000002": "AL"},
        ),
        (
            end - timedelta(seconds=60),
            {"000001": 130.0, "000002": 70.0},
            {"000001": "AL", "000002": "AL"},
        ),
        (
            end,
            {"000001": 160.0, "000002": 90.0},
            {"000001": "AL", "000002": "AL"},
        ),
    ]
    service._finalize("integrated", end, "20260710")

    payload = json.loads(service.persist_path.read_text(encoding="utf-8"))
    assert payload["source"] == "CLOSE_SAMPLER_EXACT"
    assert payload["is_estimated"] is False
    assert payload["themes"]["THEME"]["inflow_1m_eok"] == 34.0
    assert payload["themes"]["THEME"]["inflow_5m_eok"] == 52.0
    assert payload["themes"]["THEME"]["coverage_5m"] == 1.0


def test_sampler_values_replace_theme_close_flow_display():
    payload = {
        "status": {"display_basis": "LAST_CLOSE"},
        "themes": [
            {
                "theme_id": "A",
                "inflow_1m_text": "-",
                "inflow_5m_text": "-",
            }
        ],
    }
    sampler = {
        "source": "CLOSE_SAMPLER_EXACT",
        "basis_time": "2026-07-10T20:00:10",
        "trading_date": "20260710",
        "themes": {
            "A": {
                "inflow_1m_eok": 12.0,
                "inflow_5m_eok": 40.0,
                "coverage_1m": 1.0,
                "coverage_5m": 0.8,
            }
        },
    }
    result = merge_sampler_theme(payload, sampler)
    theme = result["themes"][0]
    assert theme["inflow_1m_text"] == "+12억"
    assert theme["inflow_5m_text"] == "+40억"
    assert theme["inflow_5m_coverage"] == 0.8
    assert theme["inflow_5m_is_estimated"] is False


def test_recovery_plan_uses_p0_to_p6_and_deduplicates():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.base = SimpleNamespace(
        normalize_code=lambda value: str(value or "") if len(str(value or "")) == 6 else ""
    )
    coordinator.scheduler_module = SimpleNamespace(
        _load_selected=lambda _base: "000003",
        _model_rank=lambda row, fallback: int(row.get("model_rank") or fallback),
    )
    coordinator._theme_data = lambda: (
        {
            "themes": [
                {
                    "theme_id": "T1",
                    "leaders": [{"stock_code": "000004"}],
                },
                {
                    "theme_id": "T2",
                    "leaders": [{"stock_code": "000005"}],
                },
            ]
        },
        {
            "T1": {"members": [{"stock_code": "000006"}]},
            "T2": {"members": [{"stock_code": "000007"}]},
        },
    )
    rows = [
        {"stock_code": f"{index:06d}", "model_rank": index}
        for index in range(1, 61)
    ]
    plan = coordinator._build_plan({"rows": rows})
    by_code = {item["stock_code"]: item["lane"] for item in plan}
    assert by_code["000003"] == 0
    assert by_code["000004"] == 1  # Already Top20, so the earliest lane wins.
    assert by_code["000006"] == 3
    assert by_code["000007"] == 4
    assert by_code["000021"] == 5
    assert by_code["000060"] == 6
    assert len(by_code) == len(plan)


def test_runtime_entrypoints_install_both_sides():
    root = Path(__file__).resolve().parents[1]
    collector = (root / "realtime_v2" / "collector32_large_bidask.py").read_text(
        encoding="utf-8"
    )
    platform = (root / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "prepare_collector()" in collector
    assert "install_collector(base)" in collector
    assert "install_worker(base, large)" in platform
