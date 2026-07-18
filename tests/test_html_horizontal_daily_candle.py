from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import realtime_v2
from realtime_v2 import html_horizontal_daily_candle_patch as candle_patch

ROOT = Path(__file__).resolve().parents[1]


def _sample_html() -> str:
    return r'''<!doctype html><html><head><style>
    .mini-candle { position:relative; height:14px; width:76px; margin:0 auto; background:#edf2f7; border-radius:2px; overflow:hidden; }
    .mini-candle .wick { position:absolute; left:0; width:100%; top:6px; height:2px; background:#1f2937; }
    .mini-candle .body { position:absolute; left:var(--body-left); width:var(--body-width); top:3px; height:8px; min-width:2px; background:#ef4444; }
    .mini-candle.down .body { background:#3b82f6; }
    .mini-candle.flat .body { background:#6b7280; }
</style></head><body><script>
function numeric(v){if(v===undefined||v===null||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null;}
function fmtNum(v){return String(v);}
function escapeHtml(v){return String(v);}
function candleTitle(r,o){return `시가 ${o.open}\n고가 ${o.high}\n저가 ${o.low}\n종가 ${o.close}`;}
function candleHtml(r){const o=pickOhlc(r);if(!o)return'-';const span=Math.max(1,o.high-o.low);const left=v=>Math.max(0,Math.min(100,(v-o.low)/span*100));const s=Math.min(left(o.open),left(o.close)),e=Math.max(left(o.open),left(o.close));const cls=o.close>o.open?'up':o.close<o.open?'down':'flat';return`<div class="mini-candle ${cls}" title="${escapeHtml(candleTitle(r,o))}" style="--body-left:${s.toFixed(2)}%;--body-width:${Math.max(2,e-s).toFixed(2)}%"><span class="wick"></span><span class="body"></span></div>`;}
function rowTitle(r){return '';}
</script></body></html>'''


def test_horizontal_candle_uses_caps_thick_body_and_existing_tooltip(monkeypatch):
    fake_large = SimpleNamespace(_ui_safety_patch=lambda html: html)
    monkeypatch.setattr(realtime_v2, "worker64_guarded_large", fake_large, raising=False)

    candle_patch.install()
    rendered = fake_large._ui_safety_patch(_sample_html())

    assert candle_patch.MARKER in rendered
    assert "border-left:1px solid #1f2937" in rendered
    assert "border-right:1px solid #1f2937" in rendered
    assert "background:linear-gradient(to bottom" in rendered
    assert "min-width:3px" in rendered
    assert "horizontalCandleOhlc" in rendered
    assert "const high=Math.max(rawHigh,open,close)" in rendered
    assert "const low=Math.min(rawLow,open,close)" in rendered
    assert "typeof candleTitle==='function'?candleTitle(r,o)" in rendered
    assert 'left:0; width:100%; top:6px' not in rendered
    assert rendered.count('<span class="wick"></span><span class="body"></span>') == 1


def test_horizontal_candle_contract_has_no_new_data_or_timer_path():
    source = inspect.getsource(candle_patch)
    for forbidden in (
        "requests",
        "urllib",
        "WebSocket(",
        "EventSource(",
        "Thread(",
        "setInterval(",
        "setTimeout(",
    ):
        assert forbidden not in source


def test_production_chain_installs_horizontal_candle_last():
    script = r'''
import importlib
from pathlib import Path
import realtime_v2.worker64_guarded_large_bidask
large = importlib.import_module("realtime_v2.worker64_guarded_large")
root = Path.cwd()
html = (root / "docs" / "stockboard_v2.html").read_text(encoding="utf-8-sig")
patched = large._ui_safety_patch(html)
assert "STOCKBOARD_V2_HORIZONTAL_DAILY_CANDLE_V1" in patched
assert "horizontalCandleOhlc" in patched
assert "--wick-left:" in patched
assert "--wick-width:" in patched
assert "STOCKBOARD_V2_RESPONSIVE_MOBILE_VIEW_20260717" in patched
assert "STOCKBOARD_V2_MOMENTUM_BADGE_UI_V1" in patched
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
