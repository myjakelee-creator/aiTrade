from __future__ import annotations

MARKER = "STOCKBOARD_V2_NULL_METRIC_GUARD_20260716"


def install() -> None:
    """Prevent JavaScript Number(null) from rendering missing metrics as zero."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_null_metric_guard_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched
        replacements = {
            "function numeric(v){const n=Number(v);return Number.isFinite(n)?n:null;}": (
                "function numeric(v){if(v===undefined||v===null||v==='')return null;"
                "const n=Number(v);return Number.isFinite(n)?n:null;}"
            ),
            "function clampNumber(v,min,max){const n=Number(v);return Number.isFinite(n)?Math.max(min,Math.min(max,n)):null;}": (
                "function clampNumber(v,min,max){if(v===undefined||v===null||v==='')return null;"
                "const n=Number(v);return Number.isFinite(n)?Math.max(min,Math.min(max,n)):null;}"
            ),
            "function fmtNum(v,d=0){const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d}):'-';}": (
                "function fmtNum(v,d=0){if(v===undefined||v===null||v==='')return '-';"
                "const n=Number(v);return Number.isFinite(n)?n.toLocaleString('en-US',{maximumFractionDigits:d,minimumFractionDigits:d}):'-';}"
            ),
            "function fmtRate(v){const n=Number(v);return Number.isFinite(n)?(n>=1000?`${Math.round(n).toLocaleString('en-US')}`:n.toFixed(n>=100?0:1)):'-';}": (
                "function fmtRate(v){if(v===undefined||v===null||v==='')return '-';"
                "const n=Number(v);return Number.isFinite(n)?(n>=1000?`${Math.round(n).toLocaleString('en-US')}`:n.toFixed(n>=100?0:1)):'-';}"
            ),
            "function fmtRankChange(v){const n=Number(v);if(!Number.isFinite(n))return'-';": (
                "function fmtRankChange(v){if(v===undefined||v===null||v==='')return'-';"
                "const n=Number(v);if(!Number.isFinite(n))return'-';"
            ),
            "function clsSigned(v){const n=Number(v);return!Number.isFinite(n)?'':n>0?'plus':n<0?'minus':'zero';}": (
                "function clsSigned(v){if(v===undefined||v===null||v==='')return '';"
                "const n=Number(v);return!Number.isFinite(n)?'':n>0?'plus':n<0?'minus':'zero';}"
            ),
            "function clsRatio(v){const n=Number(v);return!Number.isFinite(n)||n<=0?'zero':n>=1?'ratio-hot':'ratio-cold';}": (
                "function clsRatio(v){if(v===undefined||v===null||v==='')return '';"
                "const n=Number(v);return!Number.isFinite(n)||n<=0?'zero':n>=1?'ratio-hot':'ratio-cold';}"
            ),
            "function clsStrength(v){const n=Number(v);return!Number.isFinite(n)?'':n>=100?'strength-hot':'strength-cold';}": (
                "function clsStrength(v){if(v===undefined||v===null||v==='')return '';"
                "const n=Number(v);return!Number.isFinite(n)?'':n>=100?'strength-hot':'strength-cold';}"
            ),
        }
        for old, new in replacements.items():
            patched = patched.replace(old, new, 1)
        marker_script = f"\n  /* {MARKER} */\n"
        return patched.replace("<script>", f"<script>{marker_script}", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._null_metric_guard_installed = True
