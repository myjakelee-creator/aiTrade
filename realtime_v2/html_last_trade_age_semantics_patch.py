from __future__ import annotations

"""Stop treating a quiet stock as a broken realtime pipeline.

`price_age_sec` is the age of the stock's last accepted trade, not transport lag.
The board previously dimmed every row after three seconds without a trade and called
that state `stale`.  During aftermarket or for low-turnover stocks this is expected
and does not indicate a collector/worker/SSE problem.

This patch changes display semantics only.  It adds no request, worker calculation,
thread, timer, WebSocket, FID, or SSE cadence.
"""

MARKER = "STOCKBOARD_V2_LAST_TRADE_AGE_SEMANTICS_20260721"

_DESKTOP_OR_MOBILE_ROW_CLASS = (
    "const cls=`data-row ${sel?'selected-row ':''}${age>3?'stale ':''}`.trim();"
)
_ROW_CLASS_REPLACEMENT = "const cls=`data-row ${sel?'selected-row ':''}`.trim();"

_OLD_TOP20 = (
    "function top20Diagnostics(rows){const top=byRank(rows).slice(0,20);"
    "let maxLag=null,stale=0;top.forEach(r=>{const lag=numeric(r.fid20_lag_sec),"
    "age=numeric(r.price_age_sec);if(lag!==null)maxLag=maxLag===null?lag:Math.max(maxLag,lag);"
    "if(age!==null&&age>3)stale++;});return{maxLag,stale,realtimeRows:rows.filter("
    "r=>r.row_source==='realtime').length};}"
)
_NEW_TOP20 = (
    "function top20Diagnostics(rows){const top=byRank(rows).slice(0,20);"
    "let maxLag=null,noRecentTrade=0;top.forEach(r=>{const lag=numeric(r.fid20_lag_sec),"
    "age=numeric(r.price_age_sec);if(lag!==null)maxLag=maxLag===null?lag:Math.max(maxLag,lag);"
    "if(age!==null&&age>3)noRecentTrade++;});return{maxLag,noRecentTrade,realtimeRows:rows.filter("
    "r=>r.row_source==='realtime').length};}"
)

_OLD_LAG_TEXT = (
    "lagMetricsEl.textContent=`top20 lag ${top20.maxLag===null?'-':top20.maxLag.toFixed(1)+'s'} "
    "· stale ${top20.stale} · rt ${top20.realtimeRows}`;"
)
_NEW_LAG_TEXT = (
    "lagMetricsEl.textContent=`top20 체결시각차 ${top20.maxLag===null?'-':top20.maxLag.toFixed(1)+'s'} "
    "· 최근체결없음 ${top20.noRecentTrade} · rt ${top20.realtimeRows}`;"
)


def install() -> None:
    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_last_trade_age_semantics_installed", False):
        return

    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        row_class_count = patched.count(_DESKTOP_OR_MOBILE_ROW_CLASS)
        if row_class_count < 1:
            raise RuntimeError("last-trade row-class anchor not found")
        patched = patched.replace(_DESKTOP_OR_MOBILE_ROW_CLASS, _ROW_CLASS_REPLACEMENT)

        if _OLD_TOP20 not in patched:
            raise RuntimeError("top20 last-trade diagnostic anchor not found")
        patched = patched.replace(_OLD_TOP20, _NEW_TOP20, 1)

        if _OLD_LAG_TEXT not in patched:
            raise RuntimeError("top20 last-trade label anchor not found")
        patched = patched.replace(_OLD_LAG_TEXT, _NEW_LAG_TEXT, 1)

        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._last_trade_age_semantics_installed = True
