from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from realtime_v2 import metric_fast_sse_patch as metric

ROOT = Path(__file__).resolve().parents[1]


class FakeState:
    def __init__(self):
        import threading

        self.lock = threading.RLock()
        self.status = {
            "event_count": 5,
            "market_trading_date": "20260723",
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "trade_value_eok": 100.0,
                "trade_value_trading_date": "20260723",
                "execution_strength": 121.0,
                "bid_ask_ratio": 1.5,
            },
            "005930": {
                "stock_code": "005930",
                "trade_value_eok": 80.0,
                "trade_value_trading_date": "20260723",
                "execution_strength": 99.0,
                "bid_ask_ratio": 0.8,
            },
            "009150": {
                "stock_code": "009150",
                "trade_value_eok": 900.0,
                "trade_value_trading_date": "20260722",
            },
        }

    def rows(self, *_args, **_kwargs):
        raise AssertionError("metric stream must not call State.rows")

    def snapshot(self, *_args, **_kwargs):
        raise AssertionError("metric stream must not call State.snapshot")


def test_metric_snapshot_is_lightweight_and_ranks_current_day_only():
    payload = metric.build_metric_snapshot(
        FakeState(), limit=300, now_text=lambda: "2026-07-23T13:00:00+09:00"
    )
    by_code = {row["stock_code"]: row for row in payload["rows"]}

    assert by_code["000660"]["rank"] == 1
    assert by_code["005930"]["rank"] == 2
    assert by_code["009150"]["rank"] is None
    assert by_code["000660"]["execution_strength"] == 121.0
    assert "stock_name" not in by_code["000660"]


def test_metric_delta_contains_only_changed_rows():
    state = FakeState()
    first = metric.build_metric_snapshot(state, limit=300, now_text=lambda: "t1")
    full, fingerprints = metric.build_delta_payload(first, {}, force_full=True)
    assert full["payload_mode"] == "full"
    assert full["row_count"] == 3

    state.quotes["005930"]["execution_strength"] = 101.0
    second = metric.build_metric_snapshot(state, limit=300, now_text=lambda: "t2")
    delta, _ = metric.build_delta_payload(second, fingerprints)
    assert delta["payload_mode"] == "delta"
    assert [row["stock_code"] for row in delta["rows"]] == ["005930"]


def test_production_import_installs_metric_endpoint_and_ui():
    script = r'''
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker64_guarded_large as large
import realtime_v2.sse_latest_only_patch as full

html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
rendered = large._ui_safety_patch(html)
assert callable(getattr(base.State, "metric_fast_snapshot", None))
assert callable(getattr(base.WebHandler, "_stream_metric_fast", None))
assert "STOCKBOARD_V2_METRIC_FAST_SSE_20260723" in rendered
assert "/api/v2/metric-stream?limit=300&interval_ms=500" in rendered
assert "/api/v2/stream?limit=100&interval_ms=5000" in rendered
assert full.HEARTBEAT_SEC == 5.0
assert full.MAX_SEND_INTERVAL_MS == 5000
print("metric_fast_sse_production_import_ok")
'''
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "metric_fast_sse_production_import_ok" in completed.stdout


def test_metric_patch_does_not_add_market_data_owner():
    source = Path(metric.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "requests.",
        "urlopen(",
        "WebSocket(",
        "threading.Thread",
    ):
        assert forbidden not in source
