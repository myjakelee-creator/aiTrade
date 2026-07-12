from pathlib import Path

from realtime_v2.board_platform.http_patch import inject_shell_assets


ROOT = Path(__file__).resolve().parents[1]


def test_shell_assets_are_injected_once():
    source = "<html><head></head><body><div class='topbar'></div></body></html>"
    first = inject_shell_assets(source, "stockboard")
    second = inject_shell_assets(first, "stockboard")
    assert first == second
    assert first.count("BOARD_PLATFORM_SHELL_V1") == 1
    assert first.count("/api/v2/boards/shell.js") == 1
    assert 'content="stockboard"' in first


def test_http_paths_do_not_force_full_stockboard_recompute():
    source = (
        ROOT / "realtime_v2" / "board_platform" / "http_patch.py"
    ).read_text(encoding="utf-8")

    assert "service.set_candidate_model(model_id)\n        service.refresh(force=True)" not in source
    assert "service.request_refresh(force=not bool(service.payload_bytes))" in source
    assert "refresh_if_changed=True" in source


def test_snapshot_cache_documents_scheduler_only_ownership():
    source = (
        ROOT / "realtime_v2" / "board_platform" / "stockboard_cache.py"
    ).read_text(encoding="utf-8")

    assert '"stockboard_cache_scheduler_only": True' in source
    assert '"scheduler_only": True' in source
    assert "compute_rate_limit_skip_count" in source
    assert "coalesced_event_count" in source
