from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from realtime_v2 import price_fast_sse_recovery_patch as recovery

ROOT = Path(__file__).resolve().parents[1]


def test_recovery_contract_constants_are_conservative():
    assert recovery.PATCH_VERSION == "price_fast_sse_recovery_v1"
    assert recovery.RECONNECT_DELAY_MS == 500
    assert recovery.FULL_RESYNC_SEC == 10.0


def test_production_import_adds_explicit_reconnect_and_slower_full_resync():
    script = r'''
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker64_guarded_large as large
import realtime_v2.price_fast_sse_patch as price

html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
rendered = large._ui_safety_patch(html)

assert price.HEARTBEAT_SEC == 10.0
assert getattr(base.State, "_stockboard_price_fast_sse_recovery_version", None) == "price_fast_sse_recovery_v1"
assert "STOCKBOARD_V2_PRICE_FAST_SSE_RECOVERY_20260723" in rendered
assert "setTimeout(__sbv2ConnectPriceFastStream, 500)" in rendered
assert "failed.close()" in rendered
assert "readyState !== EventSource.CLOSED" in rendered
print("price_fast_sse_recovery_production_import_ok")
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
    assert "price_fast_sse_recovery_production_import_ok" in completed.stdout


def test_recovery_patch_does_not_add_market_data_owner():
    source = Path(recovery.__file__).read_text(encoding="utf-8")
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
