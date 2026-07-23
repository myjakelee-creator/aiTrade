from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from realtime_v2 import full_stream_relief_patch as relief

ROOT = Path(__file__).resolve().parents[1]


def test_relief_contract_constants_are_bounded():
    assert relief.PATCH_VERSION == "full_stream_relief_v1"
    assert relief.HEAVY_INTERVAL_MS == 1500
    assert relief.HEAVY_MAX_AGE_MS == 4000
    assert "trade_value_eok" in relief.LIVE_OVERLAY_FIELDS
    assert "bid_ask_ratio" in relief.LIVE_OVERLAY_FIELDS
    assert "execution_strength" in relief.LIVE_OVERLAY_FIELDS
    assert "program_net" in relief.LIVE_OVERLAY_FIELDS


def test_production_import_applies_cache_policy_and_overlay_fields():
    script = r'''
import os
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker_opening_burst_cache_patch as opening

assert os.environ.get("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS") == "1500"
assert os.environ.get("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS") == "4000"
for field in ("trade_value_eok", "trade_value_1m_eok", "bid_ask_ratio", "execution_strength", "strength_5m", "program_net"):
    assert field in opening._FAST_OVERLAY_FIELDS
assert getattr(base.State, "_stockboard_full_stream_relief_version", None) == "full_stream_relief_v1"

state = base.State(Path("data/runtime/stockboard_v2/universe.json"))
payload = state.snapshot(limit=1)
status = payload.get("status") or {}
assert status.get("full_stream_relief_version") == "full_stream_relief_v1"
assert status.get("full_stream_relief_heavy_interval_ms") == 1500
assert status.get("full_stream_relief_heavy_max_age_ms") == 4000
print("full_stream_relief_production_import_ok")
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
    assert "full_stream_relief_production_import_ok" in completed.stdout


def test_relief_patch_adds_no_market_data_owner():
    source = Path(relief.__file__).read_text(encoding="utf-8")
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
