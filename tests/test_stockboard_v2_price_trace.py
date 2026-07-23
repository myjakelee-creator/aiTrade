from __future__ import annotations

import ast
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from scripts import stockboard_v2_price_trace as trace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stockboard_v2_price_trace.py"


def test_trade_event_sample_normalizes_signed_price_and_keeps_sequence():
    event = {
        "type": "trade",
        "ts": "2026-07-22T14:00:00.100+09:00",
        "stock_code": "005930",
        "received_code": "005930",
        "collector_price_epoch": "epoch-a",
        "collector_price_seq": 12,
        "kwargs": {
            "original_registered_code": "005930_AL",
            "raw": {
                "price_raw": "-264000",
                "change_rate_raw": "+1.93",
                "trade_time_raw": "140000",
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
    assert sample["collector_price_epoch"] == "epoch-a"
    assert sample["collector_price_seq"] == 12.0
    assert sample["event_log_observe_delay_ms"] == 200.0


def test_stream_url_targets_actual_browser_price_stream():
    url = trace._stream_url(
        "http://127.0.0.1:8765/api/v2/snapshot?limit=300", 100
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/api/v2/price-stream"
    assert query["limit"] == ["300"]
    assert query["interval_ms"] == ["100"]


def test_path_classifier_uses_matched_delivery_not_last_trade_age():
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
            "row_received_at": "2026-07-22T13:59:00.000+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
        }
    ]
    assert trace._classify_path(events, rows) == "PATH_ACTIVE"
    assert trace._matched_delays_ms(events, rows) == [150.0]


def test_path_classifier_reports_real_price_stream_delay():
    events = [
        {
            "event_ts": "2026-07-22T14:00:00.100+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
        }
    ]
    rows = [
        {
            "observed_at": "2026-07-22T14:00:00.900+09:00",
            "row_received_at": "2026-07-22T14:00:00.100+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
        }
    ]
    assert trace._classify_path(events, rows) == "PRICE_STREAM_DELAY"


def test_trace_summary_reports_collector_to_price_sse_latency():
    events = [
        {
            "observed_at": "2026-07-22T14:00:00.120+09:00",
            "event_ts": "2026-07-22T14:00:00.100+09:00",
            "event_log_observe_delay_ms": 20.0,
            "price": 263500.0,
            "change_rate": 1.74,
        },
        {
            "observed_at": "2026-07-22T14:00:00.320+09:00",
            "event_ts": "2026-07-22T14:00:00.300+09:00",
            "event_log_observe_delay_ms": 20.0,
            "price": 264000.0,
            "change_rate": 1.93,
        },
    ]
    rows = [
        {
            "observed_at": "2026-07-22T14:00:00.180+09:00",
            "row_received_at": "2026-07-22T14:00:00.100+09:00",
            "price": 263500.0,
            "change_rate": 1.74,
        },
        {
            "observed_at": "2026-07-22T14:00:00.390+09:00",
            "row_received_at": "2026-07-22T14:00:00.300+09:00",
            "price": 264000.0,
            "change_rate": 1.93,
        },
    ]
    summary = trace._summarize_code("005930", "삼성전자", events, rows)
    assert summary["path_state"] == "PATH_ACTIVE"
    assert summary["collector_output_event_count"] == 2
    assert summary["price_sse_update_count"] == 2
    assert summary["collector_to_price_sse_match_count"] == 2
    assert summary["collector_to_price_sse_median_ms"] == 85.0
    assert summary["collector_to_price_sse_max_ms"] == 90.0
    assert summary["latest_event_row_match"] is True


def test_price_trace_is_operator_only_and_never_calls_kiwoom():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "operator_only_no_production_path_change" in source
    assert "events_" in source
    assert "/api/v2/price-stream" in source
    assert "/api/v2/snapshot" in source
    assert "row_transport_ms" not in source
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
