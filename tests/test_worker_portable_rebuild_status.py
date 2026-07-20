from __future__ import annotations

import json
import threading
from pathlib import Path

from realtime_v2 import worker_board_trading_date_guard as guard
from realtime_v2 import worker_portable_rebuild_status_patch as bridge


def test_context_rebuild_progress_is_bridged_into_worker_status(monkeypatch, tmp_path: Path):
    status_path = tmp_path / "context_snapshot_status.json"
    status_path.write_text(
        json.dumps(
            {
                "context_process_ready": True,
                "context_board_ready": False,
                "portable_board_refresh_status": "retry_wait",
                "portable_board_target_trading_date": "20260717",
                "portable_board_completed_count": 123,
                "portable_board_requested_count": 187,
                "portable_board_pending_count": 64,
                "portable_board_retry_attempt": 2,
                "portable_board_last_error": "temporary 429",
                "portable_board_next_retry_at": "2026-07-20T06:45:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge, "CONTEXT_STATUS_PATH", status_path)
    monkeypatch.setattr(bridge, "_last_check", 0.0)
    monkeypatch.setattr(bridge, "_last_mtime", None)
    monkeypatch.setattr(bridge, "_last_payload", {})

    class FakeGuard:
        def apply(self, state, now=None):
            state.status["board_display_basis"] = "blocked_waiting_exact_portable_snapshot"
            return False

    monkeypatch.setattr(guard, "PortableBoardGuard", FakeGuard)
    bridge.install()

    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}

    state = State()
    assert FakeGuard().apply(state) is False
    assert state.status["portable_rebuild_status_bridge_version"] == bridge.PATCH_VERSION
    assert state.status["portable_board_refresh_status"] == "retry_wait"
    assert state.status["portable_board_completed_count"] == 123
    assert state.status["portable_board_requested_count"] == 187
    assert state.status["portable_board_last_error"] == "temporary 429"
