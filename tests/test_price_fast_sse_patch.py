from __future__ import annotations

import inspect
import subprocess
import sys
import threading
from pathlib import Path

from realtime_v2 import price_fast_sse_patch as patch

ROOT = Path(__file__).resolve().parents[1]


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"trade_count": 7}
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "price": 271000,
                "trade_price": 270500,
                "change_rate": 4.03,
                "received_at": "2026-07-23T11:45:20.123+09:00",
                "trade_value_eok": 12345.6,
                "candidate_score": 99,
            },
            "000660": {
                "stock_code": "000660",
                "trade_price": 1867000,
                "change_rate": 2.02,
                "received_at": "2026-07-23T11:45:20.098+09:00",
            },
        }

    def rows(self, *_args, **_kwargs):
        raise AssertionError("price-fast SSE must not call State.rows()")

    def snapshot(self, *_args, **_kwargs):
        raise AssertionError("price-fast SSE must not call State.snapshot()")


def test_price_snapshot_copies_only_decision_critical_scalars():
    payload = patch.build_price_snapshot(
        _State(), limit=300, now_text=lambda: "2026-07-23T11:45:20.200+09:00"
    )

    assert payload["source"] == "stockboard_v2_price_fast_sse"
    assert payload["trade_count"] == 7
    assert payload["row_count"] == 2
    assert payload["rows"] == [
        {
            "stock_code": "000660",
            "price": 1867000,
            "change_rate": 2.02,
            "received_at": "2026-07-23T11:45:20.098+09:00",
        },
        {
            "stock_code": "005930",
            "price": 271000,
            "change_rate": 4.03,
            "received_at": "2026-07-23T11:45:20.123+09:00",
        },
    ]
    assert all(
        set(row) == {"stock_code", "price", "change_rate", "received_at"}
        for row in payload["rows"]
    )


def test_patch_source_has_no_new_market_data_or_background_owner():
    source = inspect.getsource(patch)
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "requests.",
        "urlopen(",
        "WebSocket(",
        "threading.Thread",
        "State.rows(",
        "State.snapshot(",
    ):
        assert forbidden not in source


def test_production_import_installs_price_route_and_browser_fast_path():
    script = r'''
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
import realtime_v2.worker64 as base
import realtime_v2.worker64_guarded_large as large

assert hasattr(base.State, "price_fast_snapshot")
assert getattr(base.State, "_stockboard_price_fast_sse_version", None) == "price_fast_sse_v1"
assert hasattr(base.WebHandler, "_stream_price_fast")

html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
rendered = large._ui_safety_patch(html)
assert "STOCKBOARD_V2_PRICE_FAST_SSE_20260723" in rendered
assert "/api/v2/price-stream?limit=300&interval_ms=100" in rendered
assert "/api/v2/stream?limit=100&interval_ms=1000" in rendered
assert "__sbv2FastPatchPriceRate(payload);" in rendered
assert "event: price" in __import__("inspect").getsource(base.WebHandler._stream_price_fast)
print("price_fast_sse_production_import_ok")
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
    assert "price_fast_sse_production_import_ok" in completed.stdout
