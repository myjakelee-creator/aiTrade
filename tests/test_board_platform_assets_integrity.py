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


def test_shell_moves_board_specific_controls_below_common_rows():
    from realtime_v2.board_platform.assets import SHELL_JS

    assert "moveLocalControls" in SHELL_JS
    assert "candidate-model-selector" in SHELL_JS
    assert "basisStatus" in SHELL_JS
    assert "copyStatus" in SHELL_JS


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
