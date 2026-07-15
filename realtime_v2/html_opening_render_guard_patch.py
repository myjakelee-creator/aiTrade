from __future__ import annotations

MARKER = "STOCKBOARD_V2_OPENING_RENDER_GUARD_20260716"


def install() -> None:
    """Keep fast price patches while limiting full-table renders at the open.

    The opening window follows the market calendar carried in the latest snapshot, so
    delayed-open special days receive the same ten-minute protection automatically.
    """

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_opening_render_guard_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched
        patched = patched.replace(
            "const __sbv2HeavyRenderIntervalMs = 500;",
            (
                "function __sbv2HeavyRenderIntervalMs(){"
                "const n=new Date();const m=n.getHours()*60+n.getMinutes();"
                "let start=540;"
                "const t=lastPayload?.status?.market_session?.windows?.regular_start"
                "||lastPayload?.market_session?.windows?.regular_start||'';"
                "const x=/^(\\d{1,2}):(\\d{2})/.exec(String(t));"
                "if(x)start=Number(x[1])*60+Number(x[2]);"
                "return m>=start&&m<start+10?1000:500;}"
            ),
            1,
        )
        patched = patched.replace(
            "now - __sbv2LastHeavyRenderAt >= __sbv2HeavyRenderIntervalMs",
            "now - __sbv2LastHeavyRenderAt >= __sbv2HeavyRenderIntervalMs()",
            1,
        )
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._opening_render_guard_installed = True
