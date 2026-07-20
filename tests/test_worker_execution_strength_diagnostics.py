from __future__ import annotations

import threading

import realtime_v2.worker_execution_strength_diagnostics_patch as diagnostics


def _rows():
    return [
        {
            "rank": 1,
            "stock_code": "000660",
            "stock_name": "SK하이닉스",
            "execution_strength": 101.2,
            "execution_strength_source": "kiwoom_rest_ws_0B_fid228",
            "execution_source_trading_date": "20260720",
            "execution_strength_received_at": "2026-07-20T09:01:02",
            "strength_5m": 95.0,
            "strength_source": "ka10046_rest_lowload",
            "strength_source_trading_date": "20260720",
        },
        {
            "rank": 2,
            "stock_code": "005930",
            "stock_name": "삼성전자",
            "execution_strength": 88.0,
            "execution_strength_source": "kiwoom_rest_ws_0B_fid228",
            "execution_source_trading_date": "20260719",
            "execution_strength_received_at": "2026-07-20T09:01:05",
            "strength_5m": 88.0,
            "strength_source": "ka10046_rest_lowload",
            "strength_source_trading_date": "20260720",
        },
        {
            "rank": 3,
            "stock_code": "000150",
            "stock_name": "두산",
            "execution_strength": 77.0,
            "execution_strength_source": "opt10046",
            "execution_strength_received_at": "2026-07-20T09:01:04",
            "strength_5m": 91.0,
            "strength_source": "ka10046_rest_lowload",
            "strength_source_trading_date": "20260720",
        },
        {
            "rank": 4,
            "stock_code": "035420",
            "stock_name": "NAVER",
            "strength_5m": 90.0,
            "strength_source": "ka10046_rest_lowload",
            "strength_source_trading_date": "20260720",
        },
    ]


def _status():
    return {
        "metric_session_state_date": "20260720",
        "five_metric_execution_date_count": 2,
        "five_metric_execution_source_count": 3,
        "five_metric_execution_stale_count": 4,
        "realtime_strength_ws_status": "subscribed",
        "realtime_strength_ws_selected_count": 100,
        "event_log_queue_size": 1,
        "dropped_trade_count": 5,
        "event_log_dropped_count": 6,
        "collector_status": {"sender_stats": {"pending_total_count": 7}},
    }


def test_build_execution_strength_diagnostics_separates_sources_and_counts():
    result = diagnostics.build_execution_strength_diagnostics(_rows(), _status())

    assert result["execution_positive_count"] == 3
    assert result["execution_trusted_fid228_count"] == 2
    assert result["execution_untrusted_positive_count"] == 1
    assert result["execution_source_date_mismatch_count"] == 1
    assert result["strength5_visible_count"] == 4
    assert result["execution_strength5_same_value_count"] == 1
    assert result["last_fid228_received_at"] == "2026-07-20T09:01:05"
    assert result["execution_hidden_date_count"] == 2
    assert result["execution_hidden_source_count"] == 3
    assert result["execution_hidden_stale_count"] == 4
    assert result["websocket_status"] == "subscribed"
    assert result["websocket_selected_count"] == 100
    assert result["collector_queue"] == 7
    assert result["worker_queue"] == 1
    assert result["drop_count"] == 5
    assert result["logdrop_count"] == 6
    assert result["source_contract_ok"] is False
    assert [sample["stock_code"] for sample in result["samples"]] == [
        "000660",
        "005930",
        "000150",
        "035420",
    ]


def test_snapshot_patch_exposes_status_and_payload_without_new_runtime_work():
    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = _status()

        def snapshot(self, limit=300):
            return {"rows": _rows()[:limit], "status": dict(self.status)}

    class Base:
        pass

    Base.State = State
    diagnostics.install(Base)
    state = State()
    payload = state.snapshot(2)
    result = payload["execution_strength_diagnostics"]

    assert result["row_count"] == 2
    assert result["execution_trusted_fid228_count"] == 2
    assert payload["status"]["execution_strength_diagnostics_installed"] is True
    assert (
        payload["status"]["execution_strength_diagnostics_version"]
        == "execution_strength_diagnostics_v1"
    )
    assert payload["status"]["execution_diag_trusted_fid228_count"] == 2
    assert payload["status"]["execution_diag_same_value_count"] == 1
