from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_board_platform_installs_fast_profile_on_actual_guarded_module():
    source = (
        ROOT / "realtime_v2" / "board_platform" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "install_http_patch(base, large)" in source
    assert 'actual_module = getattr(large, "guarded", large)' in source
    assert "install_previous_daily_fast(actual_module)" in source
    assert "install_session_metric_fast(session_metric_module)" in source
    assert "install_fast_path_profile(actual_module, base)" in source
    assert "install_model_lane_merge_optimize()" in source
    assert source.index("install_model_lane_merge_optimize()") < source.index(
        "install_fast_path_profile(actual_module, base)"
    )
    assert source.index("install_previous_daily_fast(actual_module)") < source.index(
        "install_fast_path_profile(actual_module, base)"
    )


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
        "model_submit",
        "model_merge",
        "display_order",
    ):
        assert f'"{stage}"' in source

    assert 'StockBoardModelLaneService.submit = _timed("model_submit"' in source
    assert 'STOCKBOARD_FAST_PROFILE_EVERY", 5' in source
    assert 'result["fast_profile_enabled"] = True' in source
    assert 'result["fast_profile_consistent_snapshot"] = True' in source
    assert 'f"{prefix}_snapshot_overhead_ms"' in source
    assert 'f"{prefix}_sample_id"' in source
    assert '_local.sample_current_snapshot = should_sample' in source
    assert '_local.completed_rows_profile = None' in source
    assert '_profile_payload("fast_profile", profile)' in source
