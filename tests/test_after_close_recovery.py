from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.after_close_recovery_hardening import install_module_hardening

install_module_hardening()

from realtime_v2.after_close_recovery import (  # noqa: E402
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
    assert result["minute_recovery_trading_date"] == "20260710"
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


class FakeSamplerState:
    def __init__(self):
        self.lock = threading.RLock()
        self.quotes = {
            "000001": {"stock_code": "000001"},
            "000002": {"stock_code": "000002"},
        }
        self.daily_values_by_code = {}
        self.status = {}
        self.dirty = False
        self.persist_count = 0

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.dirty = True

    def persist_daily_state_if_needed(self, force=False):
        self.persist_count += int(bool(force))
        return True


class FakeThemeService:
    def __init__(self):
        self.lock = threading.RLock()
        self.cache_version = 1
        self.snapshot = {
            "cache_version": 1,
            "status": {"display_basis": "LAST_CLOSE"},
            "themes": [
                {
                    "theme_id": "THEME",
                    "inflow_1m_text": "-",
                    "inflow_5m_text": "-",
                }
            ],
        }
        self.details = {}
        self.detail_bytes = {}
        self.snapshot_bytes = self.encode(self.snapshot)
        self.last_payload_bytes = len(self.snapshot_bytes)
        self.last_updated_at = None
        self.last_source_version = ("old",)

    @staticmethod
    def encode(payload):
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_sampler_persists_applies_and_publishes_exact_weighted_deltas(tmp_path):
    state = FakeSamplerState()
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
    service.theme_cache_service = FakeThemeService()

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

    assert state.quotes["000001"]["trade_value_1m_eok"] == 30.0
    assert state.quotes["000001"]["trade_value_5m_eok"] == 60.0
    metadata = state.quotes["000001"]["source_metadata"]["trade_value_5m_eok"]
    assert metadata["source"] == "CLOSE_SAMPLER_EXACT"
    assert metadata["quality"] == 1.0
    assert metadata["is_estimated"] is False
    assert state.persist_count == 1

    theme = service.theme_cache_service.snapshot["themes"][0]
    assert theme["inflow_1m_text"] == "+34억"
    assert theme["inflow_5m_text"] == "+52억"
    assert service.theme_cache_service.cache_version == 2
    assert service.theme_cache_service.last_source_version is None


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


def test_stale_prior_day_minute_result_is_not_complete():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.target_date = lambda: "20260713"
    coordinator.delegate = SimpleNamespace(
        _row_needs=lambda _coordinator, _row: (False, False, False)
    )
    stale = {
        "minute_recovery_status": "ok",
        "minute_recovery_trading_date": "20260710",
    }
    current = {
        "minute_recovery_status": "ok",
        "minute_recovery_trading_date": "20260713",
    }
    assert coordinator._needs(stale)[0] is True
    assert coordinator._needs(current)[0] is False


def test_recovery_plan_uses_p0_to_p6_and_deduplicates_without_detail_fetches():
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.base = SimpleNamespace(
        normalize_code=lambda value: str(value or "") if len(str(value or "")) == 6 else ""
    )
    coordinator.scheduler_module = SimpleNamespace(
        _load_selected=lambda _base: "000003",
        _model_rank=lambda row, fallback: int(row.get("model_rank") or fallback),
    )
    coordinator.theme_members = {
        "T1": (("000056", 1.0),),
        "T6": (("000057", 1.0),),
    }
    coordinator._theme_data = lambda: {
        "themes": [
            {"theme_id": "T1", "leaders": [{"stock_code": "000054"}]},
            {"theme_id": "T2", "leaders": []},
            {"theme_id": "T3", "leaders": []},
            {"theme_id": "T4", "leaders": []},
            {"theme_id": "T5", "leaders": []},
            {"theme_id": "T6", "leaders": []},
        ]
    }
    rows = [
        {"stock_code": f"{index:06d}", "model_rank": index}
        for index in range(1, 61)
    ]
    plan = coordinator._build_plan({"rows": rows})
    by_code = {item["stock_code"]: item["lane"] for item in plan}
    assert by_code["000003"] == 0
    assert by_code["000054"] == 2
    assert by_code["000056"] == 3
    assert by_code["000057"] == 4
    assert by_code["000021"] == 5
    assert by_code["000060"] == 6
    assert len(by_code) == len(plan)


def test_theme_planning_reads_only_one_shared_theme_snapshot():
    calls = []
    coordinator = AfterCloseRecoveryCoordinator.__new__(AfterCloseRecoveryCoordinator)
    coordinator.theme_url = "http://127.0.0.1:8765/api/v2/themes/snapshot"
    coordinator.theme_version = None
    coordinator.last_error = None
    coordinator.scheduler_module = SimpleNamespace(
        _read_json_url=lambda url: calls.append(url)
        or {"cache_version": 3, "themes": []}
    )
    result = coordinator._theme_data()
    assert result["cache_version"] == 3
    assert calls == [coordinator.theme_url]


def test_runtime_entrypoints_install_both_sides_and_hardening():
    root = Path(__file__).resolve().parents[1]
    collector = (root / "realtime_v2" / "collector32_large_bidask.py").read_text(
        encoding="utf-8"
    )
    platform = (root / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "install_module_hardening()" in collector
    assert "prepare_collector()" in collector
    assert "install_collector(base)" in collector
    assert "install_collector_hardening(base)" in collector
    assert "install_worker(base, large)" in platform
    assert "install_worker_hardening(base, large)" in platform
