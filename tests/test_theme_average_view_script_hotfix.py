from __future__ import annotations

from realtime_v2 import worker_theme_dual_rank_ui_patch as ui_patch
from realtime_v2.theme_average_view_script_hotfix import MARKER


def test_average_view_script_hotfix_is_inline_and_syntax_safe():
    source = ui_patch._SCRIPT

    assert MARKER in source
    assert source.count("<script>") == 1
    assert source.count("</script>") == 1
    assert "theme&&theme.average_display_rank??'-'" not in source
    assert "(theme&&theme.average_display_rank)??'-'" in source
    assert "themeViewAverage" in source
    assert "themeColumnMinimize" in source
    assert "theme-column-resizer" in source
