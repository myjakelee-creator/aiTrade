from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _stable_theme_cache_test_clock(monkeypatch, request):
    """Keep cache unit tests independent from the wall-clock market phase.

    Close-hold tests provide their own clock and are intentionally excluded.
    """

    module_name = str(getattr(request.module, "__name__", ""))
    if module_name.endswith("test_stockboard_theme_cache") or module_name.endswith(
        "test_stockboard_theme_cache_heartbeat_guard"
    ):
        from realtime_v2.theme_board_patch import ThemeCacheService

        monkeypatch.setattr(
            ThemeCacheService,
            "current_datetime",
            lambda self: datetime(2026, 7, 13, 9, 10),
        )
