from __future__ import annotations

"""Synchronize portable closed-board generations with the existing heavy cache.

The existing opening-burst cache remains the only background rebuild owner.
This module adds no thread or timer. It asks that cache for one forced rebuild
when the validated portable generation changes and suppresses stale cached rows
until that rebuild completes.
"""

import time
from typing import Any

from realtime_v2.common import now_text

PATCH_VERSION = "portable_board_cache_sync_v1"


def _status_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    status = payload.get("status")
    if not isinstance(status, dict):
        status = {}
        payload["status"] = status
    return status


def _empty_rows(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    payload["rows"] = []
    payload["row_count"] = 0
    return payload


def _request_generation_rebuild(state, generation: int) -> None:
    lock = getattr(state, "_opening_burst_cache_lock", None)
    wakeup = getattr(state, "_opening_burst_cache_wakeup", None)
    if lock is None or wakeup is None:
        return

    with lock:
        target = int(getattr(state, "_portable_cache_target_generation", 0) or 0)
        if target == generation:
            return

        built_count = int(
            getattr(state, "_opening_burst_cache_build_count", 0) or 0
        )
        inflight = bool(
            getattr(state, "_opening_burst_cache_build_inflight", False)
        )
        required_count = built_count + (2 if inflight else 1)

        state._portable_cache_target_generation = generation
        state._portable_cache_required_build_count = required_count
        state._portable_cache_ready_generation = 0

        if not getattr(state, "_opening_burst_cache_pending", False):
            state._opening_burst_cache_request_count = int(
                getattr(state, "_opening_burst_cache_request_count", 0) or 0
            ) + 1
        else:
            state._opening_burst_cache_coalesced_request_count = int(
                getattr(
                    state,
                    "_opening_burst_cache_coalesced_request_count",
                    0,
                )
                or 0
            ) + 1

        state._opening_burst_cache_pending = True
        state._opening_burst_cache_force_immediate = True
        state._opening_burst_cache_requested_reason = (
            f"portable_generation_{generation}"
        )
        state._opening_burst_cache_pending_since_mono = time.monotonic()
        state._opening_burst_cache_last_request_at = now_text()

    wakeup.set()


def _generation_ready(state, generation: int) -> bool:
    lock = getattr(state, "_opening_burst_cache_lock", None)
    if lock is None:
        return True
    with lock:
        if int(getattr(state, "_portable_cache_ready_generation", 0) or 0) == generation:
            return True

        target = int(getattr(state, "_portable_cache_target_generation", 0) or 0)
        required = int(
            getattr(state, "_portable_cache_required_build_count", 0) or 0
        )
        built = int(getattr(state, "_opening_burst_cache_build_count", 0) or 0)
        pending = bool(getattr(state, "_opening_burst_cache_pending", False))
        inflight = bool(
            getattr(state, "_opening_burst_cache_build_inflight", False)
        )
        ready = (
            target == generation
            and required > 0
            and built >= required
            and not pending
            and not inflight
        )
        if ready:
            state._portable_cache_ready_generation = generation
        return ready


def install_after_opening_cache(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_portable_cache_sync_installed",
        False,
    ):
        return

    original_snapshot = state_class.snapshot

    def snapshot(self, limit: int = 300):
        guard = getattr(self, "portable_board_guard", None)
        allowed = True
        if guard is not None:
            allowed = bool(guard.apply(self))

        with self.lock:
            basis = str(self.status.get("board_display_basis") or "")
            generation = int(self.status.get("board_portable_generation") or 0)

        if basis == "portable_exact_close" and allowed and generation > 0:
            _request_generation_rebuild(self, generation)

        payload = original_snapshot(self, limit)
        status = _status_dict(payload)
        status["portable_board_cache_sync_version"] = PATCH_VERSION
        status["portable_board_cache_generation"] = generation

        if not allowed or basis.startswith("blocked_"):
            status["portable_board_cache_sync_status"] = (
                "blocked_waiting_valid_snapshot"
            )
            with self.lock:
                self.status.update(status)
            return _empty_rows(payload)

        if basis == "portable_exact_close" and generation > 0:
            ready = _generation_ready(self, generation)
            status["portable_board_cache_sync_status"] = (
                "ready" if ready else "waiting_for_generation_rebuild"
            )
            status["portable_board_cache_ready_generation"] = int(
                getattr(self, "_portable_cache_ready_generation", 0) or 0
            )
            status["portable_board_cache_required_build_count"] = int(
                getattr(self, "_portable_cache_required_build_count", 0) or 0
            )
            if not ready:
                with self.lock:
                    self.status.update(status)
                return _empty_rows(payload)
        else:
            status["portable_board_cache_sync_status"] = "live_passthrough"

        with self.lock:
            self.status.update(status)
        return payload

    state_class.snapshot = snapshot
    state_class._stockboard_portable_cache_sync_installed = True


def prepare_install(base) -> None:
    """Wrap the later opening-cache install without changing production order."""
    import realtime_v2.worker_opening_burst_cache_patch as opening

    if getattr(opening, "_portable_cache_sync_prepare_installed", False):
        if getattr(
            getattr(base, "State", object),
            "_stockboard_opening_burst_cache_installed",
            False,
        ):
            install_after_opening_cache(base)
        return

    original_install = opening.install

    def install_with_portable_sync(target_base) -> None:
        original_install(target_base)
        install_after_opening_cache(target_base)

    opening.install = install_with_portable_sync
    opening._portable_cache_sync_prepare_installed = True

    if getattr(
        getattr(base, "State", object),
        "_stockboard_opening_burst_cache_installed",
        False,
    ):
        install_after_opening_cache(base)
