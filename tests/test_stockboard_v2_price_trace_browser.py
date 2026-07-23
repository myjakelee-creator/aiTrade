from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from scripts import stockboard_v2_price_trace_browser as browser_trace


def test_browser_trace_uses_same_100_row_limit_as_live_ui():
    url = browser_trace._browser_stream_url(
        "http://127.0.0.1:8765/api/v2/snapshot?limit=300", 100
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.path == "/api/v2/stream"
    assert query["limit"] == ["100"]
    assert query["interval_ms"] == ["100"]
    assert browser_trace.BROWSER_ROW_LIMIT == 100


def test_browser_trace_is_operator_only_wrapper():
    source = open(browser_trace.__file__, encoding="utf-8").read()
    assert "stockboard_v2_price_trace" in source
    assert "QAxWidget" not in source
    assert "SetRealReg" not in source
    assert "GetCommRealData" not in source
