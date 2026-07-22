from __future__ import annotations

import ast
from pathlib import Path

from scripts import stockboard_v2_price_trace as trace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stockboard_v2_price_trace.py"


def test_trade_event_sample_keeps_existing_collector_output_fields():
    event = {
        "type": "trade",
        "ts": "2026-07-22T14:00:00.100+09:00",
        "stock_code": "005930",
        "received_code": "005930_AL",
        "kwargs": {
            "source_code": "005930_AL",
            "raw": {
                "price_raw": "+264000",
                "change_rate_raw": "+1.93",
                "trade_time_raw": "140000",
                "cumulative_value_raw": "8605974",
            },
        },
    }
    sample = trace._trade_event_sample(
        event, "2026-07-22T14:00:00.300+09:00"
    )
    assert sample is not None
    assert sample["stock_code"] == "005930"
    assert sample["source_code"] == "005930_AL"
    assert sample["price"] == 264000.0
    assert sample["change_rate"] == 1.93
    assert sample["trade_time"] == "140000"
    assert sample["event_log_observe_delay_ms"] == 200.0


def test_path_classifier_distinguishes_missing_event_and_active_path():
    assert trace._classify_path([], []) == "NO_COLLECTOR_OUTPUT_EVENT"

    events = [
        {
            "event_ts": "2026-07-22T14:00:00.100+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
        }
    ]
    rows = [
        {
            "observed_at": "2026-07-22T14:00:00.250+09:00",
            "row_received_at": "2026-07-22T14:00:00.100+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
            "trade_time": "140000",
            "row_transport_ms": 150.0,
        }
    ]
    assert trace._classify_path(events, rows) == "PATH_ACTIVE"


def test_trace_summary_reports_collector_and_sse_intervals():
    events = [
        {
            "observed_at": "2026-07-22T14:00:00.250+09:00",
            "event_ts": "2026-07-22T14:00:00.100+09:00",
            "event_log_observe_delay_ms": 150.0,
            "price": 263500.0,
            "change_rate": 1.74,
            "trade_time": "140000",
            "source_code": "005930_AL",
        },
        {
            "observed_at": "2026-07-22T14:00:00.450+09:00",
            "event_ts": "2026-07-22T14:00:00.300+09:00",
            "event_log_observe_delay_ms": 150.0,
            "price": 264000.0,
            "change_rate": 1.93,
            "trade_time": "140000",
            "source_code": "005930_AL",
        },
    ]
    rows = [
        {
            "observed_at": "2026-07-22T14:00:00.260+09:00",
            "row_received_at": "2026-07-22T14:00:00.100+09:00",
            "price": 263500.0,
            "change_rate": 1.74,
            "trade_time": "140000",
            "row_transport_ms": 160.0,
            "source_code": "005930_AL",
        },
        {
            "observed_at": "2026-07-22T14:00:00.460+09:00",
            "row_received_at": "2026-07-22T14:00:00.300+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
            "trade_time": "140000",
            "row_transport_ms": 160.0,
            "source_code": "005930_AL",
        },
    ]
    summary = trace._summarize_code("005930", "삼성전자", events, rows)
    assert summary["path_state"] == "PATH_ACTIVE"
    assert summary["collector_output_event_count"] == 2
    assert summary["collector_price_rate_change_count"] == 1
    assert summary["collector_event_interval_median_ms"] == 200.0
    assert summary["sse_quote_update_count"] == 2
    assert summary["sse_price_rate_change_count"] == 1
    assert summary["latest_event_row_match"] is True


def test_price_trace_is_operator_only_and_never_calls_kiwoom():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "operator_only_no_production_path_change" in source
    assert "events_" in source
    assert "/api/v2/stream" in source
    assert "/api/v2/snapshot" in source
    for forbidden in (
        "kiwoom_data_provider",
        "issue_access_token",
        "fetch_trade_value_top100",
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "websocket",
    ):
        assert forbidden not in source
