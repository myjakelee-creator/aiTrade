from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_production_html_does_not_dim_quiet_stocks_as_stale():
    script = r'''
import importlib
from pathlib import Path

production = importlib.import_module("realtime_v2.worker64_guarded_large_bidask")
large = importlib.import_module("realtime_v2.worker64_guarded_large")
assert production is not None
html = Path("docs/stockboard_v2.html").read_text(encoding="utf-8-sig")
patched = large._ui_safety_patch(html)
assert "STOCKBOARD_V2_LAST_TRADE_AGE_SEMANTICS_20260721" in patched
assert "${age>3?'stale ':''}" not in patched
assert "noRecentTrade" in patched
assert "최근체결없음 ${top20.noRecentTrade}" in patched
assert "· stale ${top20.stale}" not in patched
print("last_trade_age_semantics_ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "last_trade_age_semantics_ok" in result.stdout
