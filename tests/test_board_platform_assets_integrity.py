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
    assert "#bp-common-header" in SHELL_CSS
    assert "#bp-speed-strip" in SHELL_CSS
    assert "#bp-local-toolbar" in SHELL_CSS


def test_shell_uses_one_common_fixed_header_for_every_board():
    from realtime_v2.board_platform.assets import SHELL_CSS, SHELL_JS

    assert "bp-common-header" in SHELL_JS
    assert "bp-native-topbar-hidden" in SHELL_JS
    assert "height:104px" in SHELL_CSS
    assert "height:136px" not in SHELL_CSS
    assert "flex-wrap:wrap" in SHELL_CSS


def test_common_header_text_uses_same_12px_size():
    from realtime_v2.board_platform.assets import SHELL_CSS

    assert "font:12px" in SHELL_CSS
    assert "#bp-common-header *{font-size:12px!important}" in SHELL_CSS
    assert "font-size:10px" not in SHELL_CSS
    assert "font-size:11px" not in SHELL_CSS


def test_stockboard_local_toolbar_keeps_only_real_controls():
    from realtime_v2.board_platform.assets import SHELL_JS

    assert "moveLocalControls" in SHELL_JS
    assert "ui-zoom-toggle" in SHELL_JS
    assert "column-minimize-toggle" in SHELL_JS
    assert "row-position-toggle" in SHELL_JS
    assert "candidate-model-selector" in SHELL_JS
    assert "copy-status" not in SHELL_JS
    assert "metric-mode-status" not in SHELL_JS
    assert "bar.querySelector('.small')" not in SHELL_JS


def test_themeboard_local_toolbar_does_not_show_basis_or_hts_helper():
    from realtime_v2.board_platform.assets import SHELL_JS

    assert "basisStatus" not in SHELL_JS
    assert "copyStatus" not in SHELL_JS
    assert "boardId==='themeboard'" not in SHELL_JS


def test_common_header_removes_duplicate_board_clock_and_status_text():
    from realtime_v2.board_platform.assets import SHELL_JS

    assert "bp-current-board" not in SHELL_JS
    assert "bp-common-clock" not in SHELL_JS
    assert "bp-common-status" not in SHELL_JS
    assert "mirrorStatus" not in SHELL_JS


def test_legacy_stockboard_topbar_is_force_hidden_over_id_important_rule():
    from realtime_v2.board_platform.assets import SHELL_CSS

    assert "#topbar.topbar.bp-native-topbar-hidden" in SHELL_CSS
    assert "display:none!important" in SHELL_CSS
    assert "height:0!important" in SHELL_CSS


def test_speed_metrics_have_identical_order_on_all_boards():
    from realtime_v2.board_platform.assets import SHELL_JS

    for key in (
        "bottleneck",
        "freshness",
        "recv",
        "pending",
        "drops",
        "worker_queue",
        "cpu",
        "bits",
        "compute",
        "serialize",
        "copy",
        "cache_age",
        "clients",
        "payload",
        "api_rtt",
        "browser_render",
    ):
        assert f"'{key}'" in SHELL_JS
    assert "const normalized=metricSpec.map" in SHELL_JS
