from __future__ import annotations

MARKER = "STOCKBOARD_V2_HORIZONTAL_DAILY_CANDLE_V1"

_OLD_CSS = """    .mini-candle { position:relative; height:14px; width:76px; margin:0 auto; background:#edf2f7; border-radius:2px; overflow:hidden; }
    .mini-candle .wick { position:absolute; left:0; width:100%; top:6px; height:2px; background:#1f2937; }
    .mini-candle .body { position:absolute; left:var(--body-left); width:var(--body-width); top:3px; height:8px; min-width:2px; background:#ef4444; }
    .mini-candle.down .body { background:#3b82f6; }
    .mini-candle.flat .body { background:#6b7280; }
"""

_NEW_CSS = """    .mini-candle { position:relative; height:14px; width:76px; margin:0 auto; background:#edf2f7; border-radius:2px; overflow:hidden; }
    .mini-candle .wick { position:absolute; left:var(--wick-left); width:var(--wick-width); min-width:2px; top:4px; height:6px; box-sizing:border-box; border-left:1px solid #1f2937; border-right:1px solid #1f2937; background:linear-gradient(to bottom,transparent 0,transparent 2px,#1f2937 2px,#1f2937 4px,transparent 4px,transparent 100%); z-index:1; }
    .mini-candle .body { position:absolute; left:var(--body-left); width:var(--body-width); min-width:3px; top:3px; height:8px; box-sizing:border-box; background:#ef4444; border-right:2px solid #991b1b; z-index:2; }
    .mini-candle.down .body { background:#3b82f6; border-right:0; border-left:2px solid #1d4ed8; }
    .mini-candle.flat .body { background:#6b7280; border-left:1px solid #374151; border-right:1px solid #374151; }
"""

_OLD_RATIO_FORMATTER = (
    "function fmtRatio(v){const n=Number(v);return Number.isFinite(n)&&n>0?"
    "(n>=10?'10x+':`${n.toFixed(n>=3?1:2)}x`):'-';}"
)
_NEW_RATIO_FORMATTER = (
    "function fmtRatio(v){const n=Number(v);return Number.isFinite(n)&&n>0?"
    "`${Math.round(n*100)}%`:'-';}"
)

_NEW_FUNCTIONS = r"""function horizontalCandleOhlc(r){
    const source=r.ohlc||r.realtime_ohlc||r.display_ohlc||null;
    const open=numeric(source?.open??r.open??r.open_price??r.day_open);
    const rawHigh=numeric(source?.high??r.high??r.high_price??r.day_high);
    const rawLow=numeric(source?.low??r.low??r.low_price??r.day_low);
    const close=numeric(r.price??r.trade_price??source?.current??source?.close??r.close);
    if([open,rawHigh,rawLow,close].some(value=>value===null||value<=0))return null;
    const high=Math.max(rawHigh,open,close);
    const low=Math.min(rawLow,open,close);
    return{open,high,low,close};
  }
  function candleHtml(r){
    const o=horizontalCandleOhlc(r);
    if(!o)return'-';
    const trackStart=6,trackWidth=88,minBodyWidth=4;
    const span=o.high-o.low;
    const x=value=>span>0?trackStart+Math.max(0,Math.min(1,(value-o.low)/span))*trackWidth:50;
    const lowX=span>0?x(o.low):48;
    const highX=span>0?x(o.high):52;
    const openX=x(o.open),closeX=x(o.close);
    const rawBodyStart=Math.min(openX,closeX),rawBodyEnd=Math.max(openX,closeX);
    const rawBodyWidth=rawBodyEnd-rawBodyStart;
    const bodyWidth=Math.max(minBodyWidth,rawBodyWidth);
    const bodyCenter=(openX+closeX)/2;
    const bodyLeft=Math.max(trackStart,Math.min(trackStart+trackWidth-bodyWidth,rawBodyWidth<minBodyWidth?bodyCenter-bodyWidth/2:rawBodyStart));
    const cls=o.close>o.open?'up':o.close<o.open?'down':'flat';
    const title=typeof candleTitle==='function'?candleTitle(r,o):`시가 ${fmtNum(o.open)}\n고가 ${fmtNum(o.high)}\n저가 ${fmtNum(o.low)}\n종가 ${fmtNum(o.close)}`;
    return`<div class="mini-candle ${cls}" title="${escapeHtml(title)}" style="--wick-left:${lowX.toFixed(2)}%;--wick-width:${Math.max(2,highX-lowX).toFixed(2)}%;--body-left:${bodyLeft.toFixed(2)}%;--body-width:${bodyWidth.toFixed(2)}%"><span class="wick"></span><span class="body"></span></div>`;
  }
  """


def install() -> None:
    """Install candle, ratio, age, and immutable build-identity display patches."""

    from realtime_v2.html_portable_rebuild_status_patch import (
        install as install_portable_rebuild_status,
    )

    install_portable_rebuild_status()

    from realtime_v2 import worker64_guarded_large as large

    if not getattr(large, "_horizontal_daily_candle_installed", False):
        original_ui_safety_patch = large._ui_safety_patch

        def patched_ui_safety_patch(html: str) -> str:
            patched = original_ui_safety_patch(html)
            if MARKER in patched:
                return patched
            if _OLD_CSS not in patched:
                raise RuntimeError("horizontal daily candle CSS anchor not found")
            patched = patched.replace(_OLD_CSS, _NEW_CSS, 1)

            if _OLD_RATIO_FORMATTER in patched:
                patched = patched.replace(
                    _OLD_RATIO_FORMATTER,
                    _NEW_RATIO_FORMATTER,
                    1,
                )

            start = patched.find("function candleHtml(r){")
            end = patched.find("function rowTitle(r){", start)
            if start < 0 or end < 0:
                raise RuntimeError("horizontal daily candle function anchor not found")
            patched = patched[:start] + _NEW_FUNCTIONS + patched[end:]
            return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

        large._ui_safety_patch = patched_ui_safety_patch
        large._horizontal_daily_candle_installed = True

    from realtime_v2.html_last_trade_age_semantics_patch import (
        install as install_last_trade_age_semantics,
    )

    install_last_trade_age_semantics()

    from realtime_v2.html_build_identity_patch import (
        install as install_build_identity,
    )

    install_build_identity()
