from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2 import worker_portable_cache_sync_patch as cache_sync


class _Guard:
    def __init__(self, allowed=True, basis="portable_exact_close", generation=1):
        self.allowed = allowed
        self.basis = basis
        self.generation = generation

    def apply(self, state):
        state.status["board_display_basis"] = self.basis
        state.status["board_portable_generation"] = self.generation
        return self.allowed


class _State:
    def __init__(self, guard):
        self.lock = threading.RLock()
        self.status = {}
        self.portable_board_guard = guard
        self._opening_burst_cache_lock = threading.RLock()
        self._opening_burst_cache_wakeup = threading.Event()
        self._opening_burst_cache_build_count = 0
        self._opening_burst_cache_pending = False
        self._opening_burst_cache_build_inflight = False
        self._opening_burst_cache_force_immediate = False
        self._opening_burst_cache_request_count = 0
        self._opening_burst_cache_coalesced_request_count = 0
        self._opening_burst_cache_pending_since_mono = None
        self._opening_burst_cache_last_request_at = None
        self._opening_burst_cache_requested_reason = None

    def snapshot(self, limit=300):
        return {
            "status": dict(self.status),
            "rows": [{"stock_code": "000001", "price": 120}],
            "row_count": 1,
        }


def test_generation_change_hides_rows_until_existing_heavy_cache_rebuild_finishes():
    class State(_State):
        def __init__(self):
            super().__init__(_Guard())

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    state = State()

    waiting = state.snapshot(100)
    assert waiting["rows"] == []
    assert waiting["row_count"] == 0
    assert (
        waiting["status"]["portable_board_cache_sync_status"]
        == "waiting_for_generation_rebuild"
    )
    assert state._opening_burst_cache_pending is True
    assert state._opening_burst_cache_force_immediate is True
    assert state._opening_burst_cache_wakeup.is_set()
    assert state._portable_cache_required_build_count == 1

    with state._opening_burst_cache_lock:
        state._opening_burst_cache_build_count = 1
        state._opening_burst_cache_pending = False
        state._opening_burst_cache_build_inflight = False

    ready = state.snapshot(100)
    assert ready["row_count"] == 1
    assert ready["rows"][0]["price"] == 120
    assert ready["status"]["portable_board_cache_sync_status"] == "ready"
    assert ready["status"]["portable_board_cache_ready_generation"] == 1


def test_inflight_old_build_requires_one_additional_generation_build():
    class State(_State):
        def __init__(self):
            super().__init__(_Guard())
            self._opening_burst_cache_build_inflight = True

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    state = State()

    waiting = state.snapshot(100)
    assert waiting["rows"] == []
    assert state._portable_cache_required_build_count == 2

    with state._opening_burst_cache_lock:
        state._opening_burst_cache_build_count = 1
        state._opening_burst_cache_build_inflight = False
        state._opening_burst_cache_pending = True
    still_waiting = state.snapshot(100)
    assert still_waiting["rows"] == []

    with state._opening_burst_cache_lock:
        state._opening_burst_cache_build_count = 2
        state._opening_burst_cache_pending = False
    ready = state.snapshot(100)
    assert ready["row_count"] == 1


def test_blocked_guard_never_leaks_stale_cached_rows():
    class State(_State):
        def __init__(self):
            super().__init__(
                _Guard(
                    allowed=False,
                    basis="blocked_waiting_exact_portable_snapshot",
                    generation=2,
                )
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    state = State()
    payload = state.snapshot(100)

    assert payload["rows"] == []
    assert payload["row_count"] == 0
    assert (
        payload["status"]["portable_board_cache_sync_status"]
        == "blocked_waiting_valid_snapshot"
    )


def test_live_session_bypasses_generation_wait():
    class State(_State):
        def __init__(self):
            super().__init__(
                _Guard(
                    allowed=True,
                    basis="live_session_passthrough",
                    generation=0,
                )
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    state = State()
    payload = state.snapshot(100)

    assert payload["row_count"] == 1
    assert payload["status"]["portable_board_cache_sync_status"] == "live_passthrough"
