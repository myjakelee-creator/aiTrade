from realtime_v2.board_platform.http_patch import inject_shell_assets


def test_shell_assets_are_injected_once():
    source = "<html><head></head><body><div class='topbar'></div></body></html>"
    first = inject_shell_assets(source, "stockboard")
    second = inject_shell_assets(first, "stockboard")
    assert first == second
    assert first.count("BOARD_PLATFORM_SHELL_V1") == 1
    assert first.count("/api/v2/boards/shell.js") == 1
    assert 'content="stockboard"' in first
