from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_board_platform_installs_fast_profile_on_actual_guarded_module():
    source = (
        ROOT / "realtime_v2" / "board_platform" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "install_http_patch(base, large)" in source
    assert 'actual_module = getattr(large, "guarded", large)' in source
    assert "install_fast_path_profile(actual_module, base)" in source


def test_fast_profile_exposes_stage_breakdown_without_market_logic_changes():
    source = (
        ROOT / "realtime_v2" / "board_platform" / "fast_path_profile.py"
    ).read_text(encoding="utf-8")

    for stage in (
        "quote_ensure",
        "deepcopy",
        "session",
        "ohlc_load",
        "strength_load",
        "event_age",
        "copy_previous",
        "previous_daily",
        "strength_policy",
        "orderbook_policy",
        "afterclose_restore",
        "model_merge",
        "display_order",
    ):
        assert f'"{stage}"' in source

    assert 'STOCKBOARD_FAST_PROFILE_EVERY", 5' in source
    assert 'result["fast_profile_enabled"] = True' in source
    assert "fast_profile_unaccounted_ms" not in source
    assert '_profile_payload("fast_profile", profile)' in source
