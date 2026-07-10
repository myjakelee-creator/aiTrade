from __future__ import annotations

import re

MARKER = "STOCKBOARD_V2_SCROLL_FOCUS_INDEPENDENT_V3_20260710"
LEGACY_MARKERS = (
    "STOCKBOARD_V2_SCROLL_FOCUS_INDEPENDENT_20260710",
    "STOCKBOARD_V2_SCROLL_FOCUS_INDEPENDENT_V2_20260710",
)

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

_SCROLL_GUARD_PATCH = f"""
  /* {MARKER}_BLUR */
  function __sbv2BlurFocusedBoardRow(){{
    const active = document.activeElement;
    const row = active && active.closest ? active.closest('tr[data-code]') : null;
    if(row && typeof active.blur === 'function'){{
      try{{ active.blur(); }}catch(_e){{}}
    }}
  }}
  window.addEventListener('wheel', __sbv2BlurFocusedBoardRow, {{passive:true, capture:true}});
  window.addEventListener('touchmove', __sbv2BlurFocusedBoardRow, {{passive:true, capture:true}});
  window.addEventListener('scroll', __sbv2BlurFocusedBoardRow, {{passive:true, capture:true}});
"""

_SCROLL_ANCHOR_STYLE = f"""
<style id="stockboard-v2-scroll-focus-independent-v3">
  html, body, .window, table.board, table.board tbody, table.board tr.data-row {{
    overflow-anchor: none !important;
  }}
</style>
"""


def patch_html(html: str) -> str:
    """Separate selected-row state from page scrolling.

    Ordinary stream renders never refocus the selected row. Manual scrolling
    blurs any focused table row while preserving ``selectedCode`` and the visual
    selected-row state. Arrow navigation reveals the new row once, then no
    delayed DOM-focus recovery is scheduled.
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

    # Split focus from viewport reveal.
    patched = patched.replace(_OLD_VISIBLE_FOCUS, _NEW_VISIBLE_FOCUS, 1)
    patched = patched.replace(
        "__sbv2LastNavTable = table;\n    __sbv2RefocusVisibleRow(table, code);\n    selectCodeAndLink(code, false, {focus:false});",
        "__sbv2LastNavTable = table;\n    __sbv2RefocusVisibleRow(table, code, true);\n    selectCodeAndLink(code, false, {focus:false});",
        1,
    )

    # Delayed focus recovery is the main source of scroll snap-back. Selection
    # and ArrowUp/Down navigation use selectedCode, so these retries are not
    # required for navigation continuity.
    patched = re.sub(
        r"\s*\[0,\s*80,\s*180,\s*360,\s*720,\s*1200\]\.forEach\(delay\s*=>\s*\{\s*"
        r"setTimeout\(\(\)\s*=>\s*__sbv2RefocusVisibleRow\(table,\s*code(?:,\s*false)?\),\s*delay\);\s*"
        r"\}\);",
        "\n    /* delayed refocus removed: scroll position has priority */",
        patched,
        count=1,
    )

    # Manual wheel, touch or scrollbar movement removes only DOM focus. The
    # selectedCode, blue selected-row class, S1 and HTS linkage remain intact.
    anchor = (
        "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});"
        "loadCandidateModels();loadContext();markSortHeaders();connectStream();"
    )
    if anchor in patched:
        patched = patched.replace(anchor, f"{_SCROLL_GUARD_PATCH}\n{anchor}", 1)

    if "</head>" in patched:
        patched = patched.replace("</head>", f"{_SCROLL_ANCHOR_STYLE}\n</head>", 1)

    for legacy in LEGACY_MARKERS:
        patched = patched.replace(f"/* {legacy} */", "")
    return patched


def install(large_module) -> None:
    if getattr(large_module, "_scroll_focus_independent_patch_installed", False):
        return

    original_ui_safety_patch = large_module._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        return patch_html(original_ui_safety_patch(html))

    large_module._ui_safety_patch = patched_ui_safety_patch
    large_module._scroll_focus_independent_patch_installed = True
