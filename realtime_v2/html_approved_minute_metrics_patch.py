from __future__ import annotations

MARKER = "STOCKBOARD_V2_APPROVED_MINUTE_METRICS_20260716"


def install() -> None:
    """Add the approved one-minute trade-value display without changing price patches."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_approved_minute_metrics_html_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        patched = patched.replace(
            "{ key:'amount_ratio', label:'대금비', className:'num', sort:'amount_ratio', width:62, min:48 },\n"
            "    { key:'daily_candle'",
            "{ key:'amount_ratio', label:'대금비', className:'num', sort:'amount_ratio', width:62, min:48 },\n"
            "    { key:'trade_value_1m_eok', label:'1분대금', className:'num', sort:'trade_value_1m_eok', width:82, min:66 },\n"
            "    { key:'daily_candle'",
            1,
        )
        patched = patched.replace(
            "amount_ratio: 50,\n    daily_candle:",
            "amount_ratio: 50,\n    trade_value_1m_eok: 72,\n    daily_candle:",
            1,
        )
        patched = patched.replace(
            "const amountRatioText=fmtRatio(r.amount_ratio);",
            "const amountRatioText=fmtRatio(r.amount_ratio);"
            "const minuteValue=numeric(r.trade_value_1m_eok),minutePrev=numeric(r.trade_value_prev_1m_eok),minutePct=numeric(r.trade_value_1m_ratio_pct);"
            "const minutePctText=minutePct===null?'-':minutePct>=999?'NEW':`${Math.round(minutePct)}%`;"
            "const minuteText=minuteValue===null?'-':`${minuteValue.toFixed(1)} (${minutePctText})`;"
            "const minuteClass=minutePct===null?'zero':minutePct>=100?'plus':'minus';"
            "const minuteTitle=`최근 완료 1분 ${minuteValue===null?'-':minuteValue.toFixed(1)}억\n직전 1분 ${minutePrev===null?'-':minutePrev.toFixed(1)}억\n비율 ${minutePctText}\n상태 ${r.trade_value_1m_quality||'-'}`;",
            1,
        )
        patched = patched.replace(
            "<td class=\"num ${clsRatio(r.amount_ratio)}${cellFlashClass(code,'amount_ratio',r.amount_ratio)}\">${amountRatioText}</td>\n"
            "      <td class=\"center${cellFlashClass(code,'daily_candle'",
            "<td class=\"num ${clsRatio(r.amount_ratio)}${cellFlashClass(code,'amount_ratio',r.amount_ratio)}\">${amountRatioText}</td>\n"
            "      <td class=\"num ${minuteClass}${cellFlashClass(code,'trade_value_1m_eok',`${minuteValue}|${minutePct}`)}\" title=\"${escapeHtml(minuteTitle)}\">${escapeHtml(minuteText)}</td>\n"
            "      <td class=\"center${cellFlashClass(code,'daily_candle'",
            1,
        )
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._approved_minute_metrics_html_installed = True
