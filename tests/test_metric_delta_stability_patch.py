from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

from realtime_v2 import metric_delta_stability_patch as stability
from realtime_v2 import metric_fast_sse_patch as metric

ROOT = Path(__file__).resolve().parents[1]


def test_nan_and_infinity_are_stable_for_delta_comparison():
    stability.install_runtime_wrapper()
    row = {
        "stock_code": "005930",
        "rank": 1,
        "trade_value_eok": 100.0,
        "execution_strength": float("nan"),
        "strength_5m": float("inf"),
    }
    payload = {"rows": [row], "payload_mode": "full"}
    full, fingerprints = metric.build_delta_payload(payload, {}, force_full=True)
    unchanged, _ = metric.build_delta_payload(payload, fingerprints)

    assert full["row_count"] == 1
    assert unchanged["payload_mode"] == "delta"
    assert unchanged["row_count"] == 0
    assert unchanged["rows"] == []


def test_real_metric_change_still_emits_only_changed_row():
    stability.install_runtime_wrapper()
    first = {
        "rows": [
            {"stock_code": "000660", "rank": 1, "trade_value_eok": 200.0, "execution_strength": float("nan")},
            {"stock_code": "005930", "rank": 2, "trade_value_eok": 100.0, "execution_strength": 99.0},
        ]
    }
    _, fingerprints = metric.build_delta_payload(first, {}, force_full=True)
    second = {
        "rows": [
            {"stock_code": "000660", "rank": 1, "trade_value_eok": 200.0, "execution_strength": float("nan")},
            {"stock_code": "005930", "rank": 2, "trade_value_eok": 100.0, "execution_strength": 101.0},
        ]
    }
    delta, _ = metric.build_delta_payload(second, fingerprints)

    assert delta["row_count"] == 1
    assert delta["rows"][0]["stock_code"] == "005930"


def test_production_import_installs_metric_delta_stability_before_endpoint():
    script = r'''
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.metric_fast_sse_patch as metric

assert metric.PATCH_VERSION == "metric_fast_sse_v2"
assert getattr(base.State, "_stockboard_metric_delta_stability_version", None) == "metric_delta_stability_v1"
row = {"stock_code": "005930", "rank": 1, "execution_strength": float("nan")}
payload = {"rows": [row]}
_, fp = metric.build_delta_payload(payload, {}, force_full=True)
delta, _ = metric.build_delta_payload(payload, fp)
assert delta["row_count"] == 0
print("metric_delta_stability_production_import_ok")
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
    assert "metric_delta_stability_production_import_ok" in completed.stdout


def test_stability_patch_adds_no_market_data_owner():
    source = Path(stability.__file__).read_text(encoding="utf-8")
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
