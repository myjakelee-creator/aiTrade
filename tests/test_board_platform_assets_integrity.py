from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_board_platform_assets_source_is_ascii_safe():
    path = ROOT / "realtime_v2" / "board_platform" / "assets.py"
    raw = path.read_bytes()
    raw.decode("ascii")
    assert b"BOARDS_HTML" in raw
    assert b"SHELL_JS" in raw
    assert b"SHELL_CSS" in raw


def test_board_platform_assets_are_importable_and_complete():
    from realtime_v2.board_platform.assets import BOARDS_HTML, SHELL_CSS, SHELL_JS

    assert "/api/v2/boards" in SHELL_JS
    assert "/api/v2/boards/performance" in SHELL_JS
    assert 'id="bp-board-grid"' in BOARDS_HTML
    assert "#bp-speed-strip" in SHELL_CSS


def test_shell_supports_stockboard_title_nested_in_metric_row():
    from realtime_v2.board_platform.assets import SHELL_CSS, SHELL_JS

    assert "title.parentNode" in SHELL_JS
    assert "parent.insertBefore(nav,title.nextSibling)" in SHELL_JS
    assert "bar.insertBefore(nav,title.nextSibling)" not in SHELL_JS
    assert ".bp-shell-ready #topbar.topbar" in SHELL_CSS
