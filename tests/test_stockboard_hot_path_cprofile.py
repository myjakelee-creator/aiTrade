from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_board_platform_installs_one_shot_hot_path_profiler():
    source = (ROOT / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )

    assert "install_hot_path_cprofile" in source
    assert "install_hot_path_cprofile(base)" in source
    assert source.index("install_fast_path_profile(actual_module, base)") < source.index(
        "install_hot_path_cprofile(base)"
    )


def test_hot_path_profiler_is_bounded_and_stockboard_thread_scoped():
    source = (
        ROOT / "realtime_v2" / "board_platform" / "hot_path_cprofile.py"
    ).read_text(encoding="utf-8")

    assert 'STOCKBOARD_CPROFILE_EVERY", 20' in source
    assert 'STOCKBOARD_CPROFILE_MAX_SAMPLES", 1' in source
    assert 'STOCKBOARD_CPROFILE_TOP", 20' in source
    assert 'STOCKBOARD_CPROFILE_THREAD", "stockboard-v2-shared-snapshot-cache"' in source
    assert 'current_thread = threading.current_thread().name' in source
    assert 'eligible_thread = target_thread in {"", "*"}' in source
    assert '"hot_profile_thread": thread_name' in source
    assert '"self_ms"' in source
    assert '"cumulative_ms"' in source
    assert '"primitive_calls"' in source
    assert '"total_calls"' in source
    assert '"hot_profile_top"' in source
    assert '"hot_profile_lock_candidate_ms"' in source
    assert 'str(key).startswith("hot_profile_")' in source
