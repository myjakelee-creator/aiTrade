from __future__ import annotations

import threading
from pathlib import Path

from realtime_v2.worker_opening_load_diagnostics_patch import (
    build_opening_load_payload,
)


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "realtime_v2" / "worker_opening_load_diagnostics_patch.py"
ENTRY = ROOT / "realtime_v2" / "worker_theme_selected_detail_patch.py"


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.status = {
            "metric_continuity_phase": "regular",
            "metric_continuity_reference_date": "20260714",
            "event_count": 120,
            "trade_count": 100,
            "event_log_queue_size": 3,
            "event_log_dropped_count": 0,
            "collector_status": {
                "collector_ready": True,
                "status": {
                    "running": True,
                    "login_state": "connected",
                    "realreg_succeeded": True,
                    "realreg_code_count": 100,
                    "realdata_received_count": 1000,
                    "trade_event_received_count": 900,
                },
                "sender_stats": {
                    "connected": True,
                    "pending_total_count": 2,
                    "sent_count": 800,
                    "sent_per_sec": 90.0,
                },
            },
        }
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "row_source": "realtime",
                "received_at": None,
            },
            "000660": {
                "stock_code": "000660",
                "row_source": "seed_universe",
                "received_at": None,
            },
        }


class FakeHub:
    def manifest(self):
        return {
            "state_version": 200,
            "feature_version": 20,
            "feature_row_count": 188,
            "feature_published_at": "2026-07-14T09:00:00+09:00",
            "projection_status": {
                "theme": {
                    "completed_feature_version": 19,
                    "pending_feature_version": 20,
                    "build_inflight": False,
                    "build_count": 10,
                    "publish_count": 10,
                    "coalesced_request_count": 2,
                    "stale_discard_count": 0,
                    "error_count": 0,
                    "last_build_ms": 24.0,
                },
                "theme_detail": {
                    "selected_theme_id": "140",
                    "completed_feature_version": 18,
                    "error_count": 0,
                    "last_build_ms": 1.0,
                },
                "strategy": {
                    "completed_feature_version": 20,
                    "error_count": 0,
                    "last_build_ms": 2.0,
                },
            },
        }

    def projection_snapshot(self, name):
        assert name == "theme"
        return {
            "payload": {
                "theme_count": 68,
                "calculate_ms": 25.0,
                "performance_breakdown": {
                    "aggregate_ms": 12.0,
                    "dual_rank_ms": 1.0,
                    "leader_rank_ms": 4.0,
                },
                "leader_selection_status": {"theme_count": 68},
            }
        }


def test_opening_load_payload_uses_existing_status_only():
    payload = build_opening_load_payload(FakeState(), FakeHub())

    assert payload["source"] == "stockboard_v2_opening_load_diagnostics"
    assert payload["policy"]["read_only"] is True
    assert payload["policy"]["state_snapshot_call_allowed"] is False
    assert payload["collector"]["ready"] is True
    assert payload["sender"]["pending_total_count"] == 2
    assert payload["worker"]["event_log_dropped_count"] == 0
    assert payload["worker"]["row_source_counts"] == {
        "realtime": 1,
        "seed_universe": 1,
    }
    assert payload["theme"]["feature_lag"] == 1
    assert payload["theme"]["leader_rank_ms"] == 4.0
    assert payload["theme_detail"]["feature_lag"] == 2
    assert payload["strategy"]["feature_lag"] == 0


def test_opening_load_endpoint_has_no_market_or_recording_side_effect_path():
    source = PATCH.read_text(encoding="utf-8")
    for forbidden in (
        "dynamicCall",
        "CommRqData",
        "SetRealReg",
        "QAxWidget",
        "atomic_write_json",
        "state.snapshot(",
        "projection.submit(",
        "threading.Thread",
    ):
        assert forbidden not in source
    assert '"/api/v2/hub/opening-load"' in source
    assert '"file_write_allowed": False' in source


def test_guarded_worker_installs_opening_load_diagnostics_after_hub():
    source = ENTRY.read_text(encoding="utf-8")
    assert "install_opening_load_diagnostics" in source
    assert source.rindex("install_opening_load_diagnostics(base)") > source.index(
        "ThemeSelectedDetailRuntime"
    )
