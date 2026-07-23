from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from scripts import stockboard_v2_price_trace_browser as browser_trace


def test_browser_trace_uses_live_price_stream_300_row_contract():
    url = browser_trace._browser_stream_url(
        "http://127.0.0.1:8765/api/v2/snapshot?limit=100", 100
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.path == "/api/v2/price-stream"
    assert query["limit"] == ["300"]
    assert query["interval_ms"] == ["100"]
    assert browser_trace.BROWSER_SNAPSHOT_ROW_LIMIT == 100
    assert browser_trace.BROWSER_PRICE_STREAM_ROW_LIMIT == 300


def test_delta_matcher_uses_price_transitions_and_rows_once():
    events = [
        {"event_ts": "2026-07-23T18:00:00.000+09:00", "price": 100.0, "change_rate": 1.0},
        {"event_ts": "2026-07-23T18:00:00.050+09:00", "price": 100.0, "change_rate": 1.0},
        {"event_ts": "2026-07-23T18:00:00.100+09:00", "price": 101.0, "change_rate": 1.1},
    ]
    rows = [
        {"observed_at": "2026-07-23T18:00:00.080+09:00", "row_received_at": "2026-07-23T18:00:00.050+09:00", "price": 100.0, "change_rate": 1.0},
        {"observed_at": "2026-07-23T18:00:00.160+09:00", "row_received_at": "2026-07-23T18:00:00.100+09:00", "price": 101.0, "change_rate": 1.1},
    ]

    delays = browser_trace._one_to_one_matched_delays_ms(events, rows)

    assert len(delays) == 2
    assert delays[0] == 80.0
    assert delays[1] == 60.0


def test_browser_trace_is_operator_only_wrapper():
    source = open(browser_trace.__file__, encoding="utf-8").read()
    assert "stockboard_v2_price_trace" in source
    assert "QAxWidget" not in source
    assert "SetRealReg" not in source
    assert "GetCommRealData" not in source
