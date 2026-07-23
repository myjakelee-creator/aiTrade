from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from realtime_v2 import full_stream_relief_patch as relief
from realtime_v2 import price_fast_sse_recovery_patch as recovery

ROOT = Path(__file__).resolve().parents[1]


def test_recovery_contract_constants_are_conservative():
    assert recovery.PATCH_VERSION == "price_fast_sse_recovery_v1"
    assert recovery.RECONNECT_DELAY_MS == 500
    assert recovery.FULL_RESYNC_SEC == 10.0
    assert relief.PATCH_VERSION == "full_stream_relief_v1"
    assert relief.HEAVY_INTERVAL_MS == 1500
    assert relief.HEAVY_MAX_AGE_MS == 4000


def test_production_import_adds_reconnect_and_full_stream_relief():
    script = r'''
import os
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker64_guarded_large as large
import realtime_v2.price_fast_sse_patch as price
import realtime_v2.worker_opening_burst_cache_patch as opening

html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
rendered = large._ui_safety_patch(html)

assert price.HEARTBEAT_SEC == 10.0
assert getattr(base.State, "_stockboard_price_fast_sse_recovery_version", None) == "price_fast_sse_recovery_v1"
assert getattr(base.State, "_stockboard_full_stream_relief_version", None) == "full_stream_relief_v1"
assert os.environ.get("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS") == "1500"
assert os.environ.get("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS") == "4000"
for field in ("trade_value_eok", "trade_value_1m_eok", "bid_ask_ratio", "execution_strength", "strength_5m", "program_net"):
    assert field in opening._FAST_OVERLAY_FIELDS
assert "STOCKBOARD_V2_PRICE_FAST_SSE_RECOVERY_20260723" in rendered
assert "setTimeout(__sbv2ConnectPriceFastStream, 500)" in rendered
assert "failed.close()" in rendered
assert "readyState !== EventSource.CLOSED" in rendered
print("price_fast_sse_recovery_and_full_relief_ok")
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
    assert "price_fast_sse_recovery_and_full_relief_ok" in completed.stdout


def test_transport_relief_patches_add_no_market_data_owner():
    sources = "\n".join(
        Path(module.__file__).read_text(encoding="utf-8")
        for module in (recovery, relief)
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
