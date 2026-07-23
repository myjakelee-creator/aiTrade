from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

from realtime_v2 import ratio_connection_cleanup_patch as patch

ROOT = Path(__file__).resolve().parents[1]


def test_valid_dated_previous_value_recomputes_ratio():
    row = {
        "stock_code": "005930",
        "trade_value_eok": 5000.0,
        "prev_trade_value_eok": 2500.0,
        "prev_trade_value_date": "20260722",
        "amount_ratio": 999999.0,
    }

    result = patch.validate_amount_ratio(row, "20260723")

    assert result["amount_ratio"] == 2.0
    assert result["amount_ratio_missing_reason"] is None
    assert result["amount_ratio_guard_version"] == "amount_ratio_date_guard_v1"


def test_undated_or_implausible_denominator_fails_closed():
    undated = patch.validate_amount_ratio(
        {
            "trade_value_eok": 5000.0,
            "prev_trade_value_eok": 2500.0,
            "amount_ratio": 2.0,
        },
        "20260723",
    )
    assert undated["amount_ratio"] is None
    assert undated["amount_ratio_missing_reason"] == (
        "previous_trade_value_date_unverified"
    )

    implausible = patch.validate_amount_ratio(
        {
            "trade_value_eok": 5000.0,
            "prev_trade_value_eok": 0.001,
            "prev_trade_value_date": "20260722",
        },
        "20260723",
    )
    assert implausible["amount_ratio"] is None
    assert implausible["amount_ratio_missing_reason"] == (
        "previous_trade_value_unit_mismatch_suspected"
    )


def test_cleanup_contract_does_not_touch_market_data_or_cadence():
    source = inspect.getsource(patch)
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "requests.",
        "urlopen(",
        "WebSocket(",
        "threading.Thread",
        "EventSource(",
        "setInterval(",
    ):
        assert forbidden not in source


def test_production_import_installs_ratio_guard_and_truthful_connection_badge():
    script = r'''
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker64_guarded_large as large

assert getattr(base.State, "_stockboard_amount_ratio_guard_version", None) == "amount_ratio_date_guard_v1"
html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
rendered = large._ui_safety_patch(html)
assert "STOCKBOARD_V2_CONNECTION_HEALTH_TRUTH_20260723" in rendered
truth = rendered.split("STOCKBOARD_V2_CONNECTION_HEALTH_TRUTH_20260723", 1)[1]
assert "last_event_at" not in truth.split("loadCandidateModels();", 1)[0]
assert "연결 지연" not in truth.split("loadCandidateModels();", 1)[0]
print("ratio_connection_cleanup_production_import_ok")
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
    assert "ratio_connection_cleanup_production_import_ok" in completed.stdout
