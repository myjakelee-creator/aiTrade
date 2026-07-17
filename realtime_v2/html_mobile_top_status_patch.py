from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "stockboard_view_modes.json"
MARKER = "STOCKBOARD_V2_MOBILE_TOP_STATUS_20260717"


def _load_config() -> dict[str, Any]:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("stockboard view mode config must be an object")

    raw = payload.get("mobile_performance")
    config = raw if isinstance(raw, dict) else {}
    throughput_metric = str(config.get("throughput_metric") or "recv_s")
    if throughput_metric != "recv_s":
        raise ValueError("mobile performance throughput_metric must be recv_s")

    position = str(config.get("position") or "momentum_top_row")
    if position != "momentum_top_row":
        raise ValueError("mobile performance position must be momentum_top_row")

    return {
        "enabled": bool(config.get("enabled", True)),
        "throughput_metric": throughput_metric,
        "show_stream_ms": bool(config.get("show_stream_ms", True)),
        "show_render_ms": bool(config.get("show_render_ms", True)),
        "position": position,
    }


def install() -> None:
    """Place momentum and existing browser performance values in one mobile top row.

    This patch only reuses values already calculated by the StockBoard renderer. It adds no
    request, SSE field, worker calculation, thread, observer, or periodic timer.
    """

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_mobile_top_status_patch_installed", False):
        return

    config = _load_config()
    config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched
        if not config["enabled"]:
            return patched

        momentum_anchor = '''    <div id="momentum-alert-strip" class="momentum-alert-strip">
      <span class="momentum-alert-empty">모멘텀 신호 없음</span>
    </div>'''
        top_row = '''    <div id="stockboard-mobile-top-status-row" class="stockboard-mobile-top-status-row">
      <div id="momentum-alert-strip" class="momentum-alert-strip">
        <span class="momentum-alert-empty">모멘텀 신호 없음</span>
      </div>
      <span id="stockboard-mobile-performance" class="stockboard-mobile-performance">recv -/s · stream -ms · render -ms</span>
    </div>'''
        if momentum_anchor not in patched:
            raise RuntimeError("mobile top status momentum anchor not found")
        patched = patched.replace(momentum_anchor, top_row, 1)

        element_anchor = (
            "  const momentumAlertStripEl = document.getElementById('momentum-alert-strip');"
        )
        if element_anchor not in patched:
            raise RuntimeError("mobile top status element anchor not found")
        patched = patched.replace(
            element_anchor,
            element_anchor
            + "\n  const stockboardMobilePerformanceEl = document.getElementById('stockboard-mobile-performance');",
            1,
        )

        runtime_anchor = "  function stockboardSetMobileClass(){"
        if runtime_anchor not in patched:
            raise RuntimeError("mobile top status runtime anchor not found")
        runtime = f'''  const STOCKBOARD_MOBILE_PERFORMANCE_CONFIG = {config_json};
  function stockboardUpdateMobilePerformance(rates,lag,renderMs,mode){{
    if(stockboardViewMode!=='mobile'||!stockboardMobilePerformanceEl)return;
    const config=STOCKBOARD_MOBILE_PERFORMANCE_CONFIG||{{}};
    const parts=[];
    if(config.throughput_metric==='recv_s'){{
      const recv=Number.isFinite(rates?.recvPerSec)?fmtRate(rates.recvPerSec):'-';
      parts.push(`recv ${{recv}}/s`);
    }}
    if(config.show_stream_ms){{
      const stream=Number.isFinite(lag)?Number(lag).toFixed(0):'-';
      parts.push(`${{mode==='stream'?'stream':'poll'}} ${{stream}}ms`);
    }}
    if(config.show_render_ms){{
      const render=Number.isFinite(renderMs)?Number(renderMs).toFixed(1):'-';
      parts.push(`render ${{render}}ms`);
    }}
    stockboardMobilePerformanceEl.textContent=parts.join(' · ');
  }}
'''
        patched = patched.replace(runtime_anchor, runtime + runtime_anchor, 1)

        render_anchor = (
            "renderMetricsEl.textContent=`render ${ms.toFixed(1)} ms`;"
            "renderMetricsEl.className=ms>70?'badge warn':'badge';"
        )
        if render_anchor not in patched:
            raise RuntimeError("mobile top status render anchor not found")
        patched = patched.replace(
            render_anchor,
            render_anchor + "stockboardUpdateMobilePerformance(rates,lag,ms,mode);",
            1,
        )

        style = f'''
<style id="stockboard-v2-mobile-top-status">
  /* {MARKER} */
  #stockboard-mobile-top-status-row {{ display:contents; }}
  #stockboard-mobile-performance {{ display:none; }}
  html.stockboard-mobile #stockboard-mobile-top-status-row {{
    display:flex;
    order:-10;
    align-items:center;
    gap:5px;
    width:100%;
    min-width:0;
    height:22px;
  }}
  html.stockboard-mobile #stockboard-mobile-top-status-row #momentum-alert-strip {{
    order:0 !important;
    flex:1 1 auto;
    width:auto;
    min-width:0;
    height:22px;
  }}
  html.stockboard-mobile #stockboard-mobile-performance {{
    display:inline-flex;
    flex:0 0 auto;
    align-items:center;
    height:20px;
    padding:0 3px;
    color:#374151;
    font-size:10px;
    font-weight:700;
    line-height:20px;
    white-space:nowrap;
  }}
  html.stockboard-mobile #momentum-alert-strip .momentum-alert-item {{ min-width:0; }}
  html.stockboard-mobile #momentum-alert-strip .momentum-alert-name {{
    max-width:88px;
    overflow:hidden;
    text-overflow:ellipsis;
  }}
</style>
'''
        if "</head>" not in patched:
            raise RuntimeError("mobile top status head anchor not found")
        patched = patched.replace("</head>", style + "</head>", 1)
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._mobile_top_status_patch_installed = True
