from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2 import worker_portable_cache_sync_patch as cache_sync


class _Guard:
    def __init__(
        self,
        *,
        allowed: bool = True,
        basis: str = "portable_exact_close",
        generation: int = 3,
        source_date: str = "20260716",
    ) -> None:
        self.allowed = allowed
        self.basis = basis
        self.generation = generation
        self.source_date = source_date

    def apply(self, state) -> bool:
        state.status["board_display_basis"] = self.basis
        state.status["board_portable_generation"] = self.generation
        state.status["board_source_trading_date"] = self.source_date
        return self.allowed


class _State:
    def __init__(
        self,
        *,
        guard: _Guard,
        cached_generation: int,
        cached_date: str = "20260716",
        cached_basis: str = "portable_exact_close",
    ) -> None:
        self.lock = threading.RLock()
        self.status = {}
        self.portable_board_guard = guard
        self._opening_burst_cache_lock = threading.RLock()
        self._opening_burst_cache_signature = (
            0,
            0,
            0,
            2,
            "",
            (7, cached_generation, cached_date, cached_basis),
        )

    def snapshot(self, limit=300):
        return {
            "status": dict(self.status),
            "rows": [{"stock_code": "000001", "price": 120}],
            "row_count": 1,
        }


def test_signature_hook_includes_generation_date_and_basis():
    opening = SimpleNamespace(_display_version=lambda _state: 7)
    cache_sync._install_opening_signature_hook(opening)

    state = SimpleNamespace(
        status={
            "board_portable_generation": 3,
            "board_source_trading_date": "20260716",
            "board_display_basis": "portable_exact_close",
        }
    )
    component = opening._display_version(state)

    assert component == (
        7,
        3,
        "20260716",
        "portable_exact_close",
    )
    assert opening._portable_generation_signature_installed is True


def test_matching_generation_releases_rows_without_build_count_prediction():
    class State(_State):
        def __init__(self):
            super().__init__(
                guard=_Guard(),
                cached_generation=3,
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    payload = State().snapshot(100)

    assert payload["row_count"] == 1
    assert payload["rows"][0]["price"] == 120
    assert payload["status"]["portable_board_cache_sync_status"] == "ready"
    assert payload["status"]["portable_board_cache_ready_generation"] == 3
    assert "portable_board_cache_required_build_count" not in payload["status"]


def test_stale_generation_is_hidden_until_normal_structure_rebuild_finishes():
    class State(_State):
        def __init__(self):
            super().__init__(
                guard=_Guard(generation=3),
                cached_generation=2,
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    payload = State().snapshot(100)

    assert payload["rows"] == []
    assert payload["row_count"] == 0
    assert (
        payload["status"]["portable_board_cache_sync_status"]
        == "waiting_for_generation_rebuild"
    )
    assert payload["status"]["portable_board_cache_generation"] == 3
    assert payload["status"]["portable_board_cache_ready_generation"] == 2


def test_source_date_or_basis_mismatch_is_also_hidden():
    class State(_State):
        def __init__(self):
            super().__init__(
                guard=_Guard(),
                cached_generation=3,
                cached_date="20260715",
                cached_basis="portable_exact_close",
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    payload = State().snapshot(100)

    assert payload["row_count"] == 0
    assert (
        payload["status"]["portable_board_cache_sync_status"]
        == "waiting_for_generation_rebuild"
    )


def test_blocked_guard_never_leaks_cached_rows():
    class State(_State):
        def __init__(self):
            super().__init__(
                guard=_Guard(
                    allowed=False,
                    basis="blocked_waiting_exact_portable_snapshot",
                    generation=4,
                ),
                cached_generation=3,
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    payload = State().snapshot(100)

    assert payload["rows"] == []
    assert payload["row_count"] == 0
    assert (
        payload["status"]["portable_board_cache_sync_status"]
        == "blocked_waiting_valid_snapshot"
    )


def test_live_session_bypasses_portable_generation_gate():
    class State(_State):
        def __init__(self):
            super().__init__(
                guard=_Guard(
                    basis="live_session_passthrough",
                    generation=0,
                    source_date="20260720",
                ),
                cached_generation=0,
                cached_date="",
                cached_basis="",
            )

    base = SimpleNamespace(State=State)
    cache_sync.install_after_opening_cache(base)
    payload = State().snapshot(100)

    assert payload["row_count"] == 1
    assert payload["status"]["portable_board_cache_sync_status"] == (
        "live_passthrough"
    )
