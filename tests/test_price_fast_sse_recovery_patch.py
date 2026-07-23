from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from realtime_v2 import full_stream_relief_patch as relief
from realtime_v2 import metric_fast_sse_patch as metric
from realtime_v2 import price_fast_sse_recovery_patch as recovery

ROOT = Path(__file__).resolve().parents[1]


def test_recovery_contract_constants_are_conservative():
    assert recovery.PATCH_VERSION == "price_fast_sse_recovery_v1"
    assert recovery.RECONNECT_DELAY_MS == 500
    assert recovery.FULL_RESYNC_SEC == 10.0
    assert relief.PATCH_VERSION == "full_stream_relief_v1"
    assert relief.HEAVY_INTERVAL_MS == 1500
    assert relief.HEAVY_MAX_AGE_MS == 4000
    assert metric.PATCH_VERSION == "metric_fast_sse_v1"
    assert metric.DEFAULT_INTERVAL_MS == 500


def test_production_import_adds_reconnect_full_relief_and_metric_stream():
    script = r'''
import os
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker64_guarded_large as large
import realtime_v2.price_fast_sse_patch as price
import realtime_v2.sse_latest_only_patch as full
import realtime_v2.worker_opening_burst_cache_patch as opening

html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
rendered = large._ui_safety_patch(html)

assert price.HEARTBEAT_SEC == 10.0
assert full.HEARTBEAT_SEC == 5.0
assert full.MAX_SEND_INTERVAL_MS == 5000
assert getattr(base.State, "_stockboard_price_fast_sse_recovery_version", None) == "price_fast_sse_recovery_v1"
assert getattr(base.State, "_stockboard_full_stream_relief_version", None) == "full_stream_relief_v1"
assert callable(getattr(base.State, "metric_fast_snapshot", None))
assert callable(getattr(base.WebHandler, "_stream_metric_fast", None))
assert os.environ.get("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS") == "1500"
assert os.environ.get("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS") == "4000"
for field in ("trade_value_eok", "trade_value_1m_eok", "bid_ask_ratio", "execution_strength", "strength_5m", "program_net"):
    assert field in opening._FAST_OVERLAY_FIELDS
assert "STOCKBOARD_V2_PRICE_FAST_SSE_RECOVERY_20260723" in rendered
assert "STOCKBOARD_V2_METRIC_FAST_SSE_20260723" in rendered
assert "/api/v2/metric-stream?limit=300&interval_ms=500" in rendered
assert "interval_ms=5000" in rendered
assert "setTimeout(__sbv2ConnectPriceFastStream, 500)" in rendered
assert "failed.close()" in rendered
assert "readyState !== EventSource.CLOSED" in rendered
print("price_metric_stream_and_full_relief_ok")
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
    assert "price_metric_stream_and_full_relief_ok" in completed.stdout


def test_transport_relief_patches_add_no_market_data_owner():
    sources = "\n".join(
        Path(module.__file__).read_text(encoding="utf-8")
        for module in (recovery, relief, metric)
    )
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "requests.",
        "urlopen(",
        "WebSocket(",
        "threading.Thread",
    ):
        assert forbidden not in sources
