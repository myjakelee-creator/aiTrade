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

    position = str(config.get("position") or "after_candidate_selector")
    if position != "after_candidate_selector":
        raise ValueError("mobile performance position must be after_candidate_selector")

    display_mode = str(config.get("display_mode") or "separate_badges")
    if display_mode != "separate_badges":
        raise ValueError("mobile performance display_mode must be separate_badges")

    return {
        "enabled": bool(config.get("enabled", True)),
        "throughput_metric": throughput_metric,
        "show_stream_ms": bool(config.get("show_stream_ms", True)),
        "show_render_ms": bool(config.get("show_render_ms", True)),
        "position": position,
        "display_mode": display_mode,
        "wrap": bool(config.get("wrap", True)),
    }


def install() -> None:
    """Keep momentum full-width and reuse existing performance values as mobile badges.

    The patch adds no request, SSE field, worker calculation, thread, observer, or periodic
    timer. The topbar's existing flex-wrap handles narrow widths automatically.
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

        candidate_anchor = (
            '<label class="badge">선발기준 '
            '<select id="candidate-model-selector" class="control" disabled>'
            '<option value="">모델 설정 로딩</option></select></label>'
        )
        performance_badges = candidate_anchor + (
            '<span id="stockboard-mobile-recv" '
            'class="badge stockboard-mobile-performance-badge">recv/s -</span>'
            '<span id="stockboard-mobile-stream" '
            'class="badge stockboard-mobile-performance-badge">stream - ms</span>'
            '<span id="stockboard-mobile-render" '
            'class="badge stockboard-mobile-performance-badge">render - ms</span>'
        )
        if candidate_anchor not in patched:
            raise RuntimeError("mobile performance candidate selector anchor not found")
        patched = patched.replace(candidate_anchor, performance_badges, 1)

        element_anchor = (
            "  const candidateModelSelector = document.getElementById('candidate-model-selector');"
        )
        if element_anchor not in patched:
            raise RuntimeError("mobile performance element anchor not found")
        patched = patched.replace(
            element_anchor,
            element_anchor
            + "\n  const stockboardMobileRecvEl = document.getElementById('stockboard-mobile-recv');"
            + "\n  const stockboardMobileStreamEl = document.getElementById('stockboard-mobile-stream');"
            + "\n  const stockboardMobileRenderEl = document.getElementById('stockboard-mobile-render');",
            1,
        )

        runtime_anchor = "  function stockboardSetMobileClass(){"
        if runtime_anchor not in patched:
            raise RuntimeError("mobile performance runtime anchor not found")
        runtime = f'''  const STOCKBOARD_MOBILE_PERFORMANCE_CONFIG = {config_json};
  function stockboardUpdateMobilePerformance(rates,lag,renderMs,mode){{
    if(stockboardViewMode!=='mobile')return;
    const config=STOCKBOARD_MOBILE_PERFORMANCE_CONFIG||{{}};
    if(stockboardMobileRecvEl&&config.throughput_metric==='recv_s'){{
      const recv=Number.isFinite(rates?.recvPerSec)?fmtRate(rates.recvPerSec):'-';
      stockboardMobileRecvEl.textContent=`recv/s ${{recv}}`;
    }}
    if(stockboardMobileStreamEl&&config.show_stream_ms){{
      const stream=Number.isFinite(lag)?Number(lag).toFixed(0):'-';
      stockboardMobileStreamEl.textContent=`${{mode==='stream'?'stream':'poll'}} ${{stream}} ms`;
    }}
    if(stockboardMobileRenderEl&&config.show_render_ms){{
      const render=Number.isFinite(renderMs)?Number(renderMs).toFixed(1):'-';
      stockboardMobileRenderEl.textContent=`render ${{render}} ms`;
    }}
  }}
'''
        patched = patched.replace(runtime_anchor, runtime + runtime_anchor, 1)

        render_anchor = (
            "renderMetricsEl.textContent=`render ${ms.toFixed(1)} ms`;"
            "renderMetricsEl.className=ms>70?'badge warn':'badge';"
        )
        if render_anchor not in patched:
            raise RuntimeError("mobile performance render anchor not found")
        patched = patched.replace(
            render_anchor,
            render_anchor + "stockboardUpdateMobilePerformance(rates,lag,ms,mode);",
            1,
        )

        style = f'''
<style id="stockboard-v2-mobile-top-status">
  /* {MARKER} */
  .stockboard-mobile-performance-badge {{ display:none; }}
  html.stockboard-mobile #momentum-alert-strip {{
    order:-20 !important;
    flex:0 0 100% !important;
    width:100% !important;
    min-width:0 !important;
    max-width:100% !important;
    height:22px;
  }}
  html.stockboard-mobile .stockboard-mobile-performance-badge {{
    display:inline-flex;
    flex:0 0 auto;
    align-items:center;
    font-size:inherit;
    font-weight:inherit;
    white-space:nowrap;
  }}
</style>
'''
        if "</head>" not in patched:
            raise RuntimeError("mobile performance head anchor not found")
        patched = patched.replace("</head>", style + "</head>", 1)
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._mobile_top_status_patch_installed = True
