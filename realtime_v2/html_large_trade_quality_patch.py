from __future__ import annotations

MARKER = "STOCKBOARD_V2_LARGE_TRADE_QUALITY_20260716"


def install() -> None:
    """Prefix uncertain reconnect totals with ~ and expose the quality in a tooltip."""

    from realtime_v2.html_momentum_badge_patch import install as install_momentum_badges

    install_momentum_badges()

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
            "const largeText=largeUncertain&&largeBase!=='-'?`~${largeBase}`:largeBase;"
            "const largeTitle=`5천만원 이상 당일 누적\\n품질 ${largeQuality||'-'}\\n~ 표시는 재접속 공백 가능`;",
            1,
        )
        patched = patched.replace(
            "<td class=\"num ${clsSigned(r.large_trade_net_count)}${cellFlashClass(code,'large_trade_net_count',r.large_trade_net_count)}\">${largeText}</td>",
            "<td class=\"num ${clsSigned(r.large_trade_net_count)}${cellFlashClass(code,'large_trade_net_count',r.large_trade_net_count)}\" title=\"${escapeHtml(largeTitle)}\">${largeText}</td>",
            1,
        )
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._large_trade_quality_html_installed = True
