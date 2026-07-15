from __future__ import annotations


_MARKER = "STOCKBOARD_THEMEBOARD_SHARED_SHELL_V1"
_TITLE_ANCHOR = '<span class="title">StockBoard v2 Realtime</span>'

_STYLE = r'''
<style id="stockboard-themeboard-shared-shell-v1">
  /* STOCKBOARD_THEMEBOARD_SHARED_SHELL_V1 */
  #topbar .board-shell-tab {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-height: 22px;
    padding: 1px 7px;
    border: 1px solid #9aa8b5;
    border-radius: 3px;
    background: #edf3f8;
    color: #111827;
    font: 800 12px "Malgun Gothic", Arial, sans-serif;
    line-height: 1.2;
    text-decoration: none;
    white-space: nowrap;
    box-sizing: border-box;
  }
  #topbar .board-shell-tab.active {
    color: #fff;
    border-color: #1d4ed8;
    background: #1d4ed8;
  }
  #topbar .board-shell-tab.disabled {
    color: #94a3b8;
    background: #f8fafc;
    pointer-events: none;
  }
  #topbar .board-shell-new-window {
    min-height: 22px;
    padding: 1px 7px;
    font-weight: 800;
  }
</style>
'''

_NAV = (
    '<a class="board-shell-tab active" href="/">StockBoard</a>'
    '<a class="board-shell-tab" href="/theme">ThemeBoard</a>'
    '<span class="board-shell-tab disabled">StrategyBoard</span>'
    '<button type="button" class="control board-shell-new-window" '
    'onclick="window.open(\'/\',\'aitrade-stockboard\')">새 창</button>'
)


def install(large) -> None:
    """Give StockBoard the same lightweight navigation shell as ThemeBoard.

    The patch changes HTML presentation only. It does not open another SSE, preload a
    board, calculate market data, or call a TR. The linked board starts only after the
    user explicitly navigates to it.
    """

    if getattr(large, "_shared_board_shell_patch_installed", False):
        return

    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if _MARKER in patched:
            return patched
        if _TITLE_ANCHOR in patched:
            patched = patched.replace(
                _TITLE_ANCHOR,
                f"{_TITLE_ANCHOR}{_NAV}",
                1,
            )
        if "</head>" in patched:
            patched = patched.replace("</head>", f"{_STYLE}\n</head>", 1)
        return patched

    large._ui_safety_patch = patched_ui_safety_patch
    large._shared_board_shell_patch_installed = True
