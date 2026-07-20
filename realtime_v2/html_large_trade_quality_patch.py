from __future__ import annotations

MARKER = "STOCKBOARD_V2_LARGE_TRADE_QUALITY_20260716"


def install() -> None:
    """Prefix uncertain reconnect totals with ~ without adding a tooltip."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_large_trade_quality_html_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched
        patched = patched.replace(
            "const largeText=fmtNum(r.large_trade_net_count,0);",
            "const largeQuality=String(r.large_trade_quality||r.large_trade_status||'');"
            "const largeBase=fmtNum(r.large_trade_net_count,0);"
            "const largeUncertain=['GAP_POSSIBLE','PARTIAL','partial_recent_page_since_activation'].includes(largeQuality);"
            "const largeText=largeUncertain&&largeBase!=='-'?`~${largeBase}`:largeBase;",
            1,
        )
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._large_trade_quality_html_installed = True
