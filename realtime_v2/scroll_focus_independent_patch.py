from __future__ import annotations

import re

MARKER = "STOCKBOARD_V2_SCROLL_FOCUS_INDEPENDENT_V2_20260710"
LEGACY_MARKER = "STOCKBOARD_V2_SCROLL_FOCUS_INDEPENDENT_20260710"


_REFOCUS_PATTERN = re.compile(
    r"const refocus=opt\.focusSelected\|\|\(navigationActive&&document\.activeElement&&"
    r"document\.activeElement\.closest&&document\.activeElement\.closest\('tr\[data-code\]'\)\);"
)

_OLD_VISIBLE_FOCUS = """  function __sbv2RefocusVisibleRow(table, code){
    const row = table && table.querySelector ? table.querySelector(`tbody tr[data-code="${code}"]`) : null;
    if(row && typeof row.focus === 'function'){
      row.focus({preventScroll:true});
      if(typeof row.scrollIntoView === 'function') row.scrollIntoView({block:'nearest', inline:'nearest'});
      return true;
    }
    return false;
  }"""

_NEW_VISIBLE_FOCUS = f"""  function __sbv2RefocusVisibleRow(table, code, reveal=false){{
    const row = table && table.querySelector ? table.querySelector(`tbody tr[data-code="${{code}}"]`) : null;
    if(row && typeof row.focus === 'function'){{
      row.focus({{preventScroll:true}});
      if(reveal === true && typeof row.scrollIntoView === 'function') row.scrollIntoView({{block:'nearest', inline:'nearest'}});
      return true;
    }}
    return false;
  }}
  /* {MARKER} */"""


def patch_html(html: str) -> str:
    """Keep selected-row state independent from the page's vertical scroll.

    Ordinary stream renders and delayed refocus attempts may restore DOM focus but
    must not move the viewport. Arrow navigation reveals the newly selected row
    once, then all delayed focus recovery uses ``preventScroll`` only.
    """

    if MARKER in html:
        return html

    replacement = f"const refocus=opt.focusSelected===true;/* {MARKER} */"
    patched, count = _REFOCUS_PATTERN.subn(replacement, html, count=1)
    if not count:
        old = (
            "const refocus=opt.focusSelected||(navigationActive&&document.activeElement&&"
            "document.activeElement.closest&&document.activeElement.closest('tr[data-code]'));"
        )
        patched = html.replace(old, replacement, 1)

    # The UI safety patch historically called scrollIntoView for every refocus,
    # including delayed recovery calls. Split focus from reveal so scrolling is
    # only performed for the user's explicit ArrowUp/ArrowDown movement.
    patched = patched.replace(_OLD_VISIBLE_FOCUS, _NEW_VISIBLE_FOCUS, 1)
    patched = patched.replace(
        "__sbv2LastNavTable = table;\n    __sbv2RefocusVisibleRow(table, code);\n    selectCodeAndLink(code, false, {focus:false});",
        "__sbv2LastNavTable = table;\n    __sbv2RefocusVisibleRow(table, code, true);\n    selectCodeAndLink(code, false, {focus:false});",
        1,
    )
    patched = patched.replace(
        "setTimeout(() => __sbv2RefocusVisibleRow(table, code), delay);",
        "setTimeout(() => __sbv2RefocusVisibleRow(table, code, false), delay);",
        1,
    )

    # Remove the legacy marker text if present so the generated HTML clearly
    # reports only the active V2 behavior.
    patched = patched.replace(f"/* {LEGACY_MARKER} */", "")
    return patched


def install(large_module) -> None:
    if getattr(large_module, "_scroll_focus_independent_patch_installed", False):
        return

    original_ui_safety_patch = large_module._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        return patch_html(original_ui_safety_patch(html))

    large_module._ui_safety_patch = patched_ui_safety_patch
    large_module._scroll_focus_independent_patch_installed = True
