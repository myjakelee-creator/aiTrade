from __future__ import annotations

MARKER = "STOCKBOARD_V2_EXECUTION_STRENGTH_LABEL_20260716"


def install() -> None:
    """Use Kiwoom's original label 체결강도 throughout the StockBoard UI."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_execution_strength_label_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched
        patched = patched.replace("순간강도", "체결강도")
        patched = patched.replace("· 순간 ", "· 체결 ")
        marker = f"\n  /* {MARKER} */\n"
        return patched.replace("<script>", f"<script>{marker}", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._execution_strength_label_installed = True
