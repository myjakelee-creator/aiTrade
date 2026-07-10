from __future__ import annotations

import re

MARKER = "STOCKBOARD_V2_SCROLL_FOCUS_INDEPENDENT_20260710"


_REF0CUS_PATTERN = re.compile(
    r"const refocus=opt\.focusSelected\|\|\(navigationActive&&document\.activeElement&&"
    r"document\.activeElement\.closest&&document\.activeElement\.closest\('tr\[data-code\]'\)\);"
)


def patch_html(html: str) -> str:
    """Allow vertical scrolling to remain independent from the selected row.

    Ordinary stream renders must not refocus the selected row merely because a
    table row currently owns DOM focus. Explicit row clicks and ArrowUp/Down
    navigation still pass ``focusSelected=true`` and therefore keep their
    existing behavior.
    """

    if MARKER in html:
        return html

    replacement = f"const refocus=opt.focusSelected===true;/* {MARKER} */"
    patched, count = _REF0CUS_PATTERN.subn(replacement, html, count=1)
    if count:
        return patched

    # Fail open for minor whitespace differences in the generated HTML.
    old = (
        "const refocus=opt.focusSelected||(navigationActive&&document.activeElement&&"
        "document.activeElement.closest&&document.activeElement.closest('tr[data-code]'));"
    )
    return html.replace(old, replacement, 1)


def install(large_module) -> None:
    if getattr(large_module, "_scroll_focus_independent_patch_installed", False):
        return

    original_ui_safety_patch = large_module._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        return patch_html(original_ui_safety_patch(html))

    large_module._ui_safety_patch = patched_ui_safety_patch
    large_module._scroll_focus_independent_patch_installed = True
