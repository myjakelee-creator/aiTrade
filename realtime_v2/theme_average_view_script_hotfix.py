from __future__ import annotations

from realtime_v2 import theme_average_view_layout_patch as layout_patch
from realtime_v2 import worker_theme_dual_rank_ui_patch as ui_patch


MARKER = "THEMEBOARD_AVERAGE_VIEW_SCRIPT_HOTFIX_20260714"


def _script_body() -> str:
    source = layout_patch._SCRIPT_EXTENSION
    # JavaScript forbids mixing && and ?? without explicit parentheses. The old
    # extension contained one such expression, so Chrome rejected the entire script
    # even though the server had injected it successfully.
    source = source.replace(
        "theme&&theme.average_display_rank??'-'",
        "(theme&&theme.average_display_rank)??'-'",
    )
    start = source.find("<script>")
    end = source.rfind("</script>")
    if start < 0 or end <= start:
        raise RuntimeError("average view script wrapper is malformed")
    body = source[start + len("<script>") : end]
    return f"\n/* {MARKER} */\n{body.strip()}\n"


def install_script_hotfix() -> None:
    if getattr(ui_patch, "_stockboard_average_view_script_hotfix_installed", False):
        return

    if layout_patch.MARKER not in ui_patch._STYLE:
        ui_patch._STYLE += layout_patch._STYLE_EXTENSION

    body = _script_body()
    if MARKER not in ui_patch._SCRIPT:
        closing = ui_patch._SCRIPT.rfind("</script>")
        if closing < 0:
            raise RuntimeError("ThemeBoard UI script closing tag was not found")
        ui_patch._SCRIPT = (
            ui_patch._SCRIPT[:closing]
            + body
            + ui_patch._SCRIPT[closing:]
        )

    # Prevent the older wrapper from appending the invalid second script later.
    ui_patch._stockboard_average_view_script_hotfix_installed = True
