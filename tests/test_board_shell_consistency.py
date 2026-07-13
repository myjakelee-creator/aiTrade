from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
THEME_HTML = ROOT / "docs" / "themeboard.html"
SHELL_PATCH = ROOT / "realtime_v2" / "worker_board_shell_patch.py"
WORKER_ENTRY = ROOT / "realtime_v2" / "worker64_guarded_large_bidask.py"


def test_stockboard_and_themeboard_share_navigation_labels_and_states():
    theme = THEME_HTML.read_text(encoding="utf-8")
    shell = SHELL_PATCH.read_text(encoding="utf-8")

    for label in ("StockBoard", "ThemeBoard", "StrategyBoard", "새 창"):
        assert label in theme
        assert label in shell

    assert 'class="board-shell-tab active" href="/theme"' in theme
    assert 'class=\"board-shell-tab active\" href=\"/\"' in shell
    assert 'class="board-shell-tab disabled"' in theme
    assert 'class=\"board-shell-tab disabled\"' in shell


def test_themeboard_topbar_matches_stockboard_shell_geometry():
    theme = THEME_HTML.read_text(encoding="utf-8")

    assert 'id="topbar" class="topbar"' in theme
    assert theme.count('class="metric-row"') == 3
    assert "height:112px" in theme
    assert "min-height:112px" in theme
    assert "max-height:112px" in theme
    assert "background:#dde7f1" in theme
    assert "border:1px solid #9aa8b5" in theme
    assert "font:12px \"Malgun Gothic\",Arial,sans-serif" in theme


def test_stockboard_shell_patch_is_display_only_and_installed_after_ui_patches():
    shell = SHELL_PATCH.read_text(encoding="utf-8")
    entry = WORKER_ENTRY.read_text(encoding="utf-8")

    assert "STOCKBOARD_THEMEBOARD_SHARED_SHELL_V1" in shell
    assert "dynamicCall" not in shell
    assert "kiwoom_data_provider" not in shell
    assert "EventSource" not in shell
    assert "fetch(" not in shell
    assert "install_board_shell(large)" in entry

    display_index = entry.index("_install_display50_fast_price_patch_fail_open()")
    shell_index = entry.index("_install_shared_board_shell_fail_open()", display_index)
    hub_index = entry.index("_install_board_data_hub_fail_open()", shell_index)
    assert display_index < shell_index < hub_index


def test_themeboard_header_keeps_display_only_policy():
    theme = THEME_HTML.read_text(encoding="utf-8")

    assert "HTML 계산 없음" in theme
    assert "ThemeBoard 신규 TR 없음" in theme
    assert "FOCUS" not in theme
    assert "/api/v2/hub/theme/stream?interval_ms=1000" in theme
    assert "/api/v2/hub/theme/detail?theme_id=" in theme
