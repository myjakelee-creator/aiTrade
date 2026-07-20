from __future__ import annotations

"""Integrate portable closed-board generations into the existing heavy cache.

The existing opening-burst cache remains the only background rebuild owner. This
module adds no thread, timer, or build-count prediction. Before that cache is
installed, its structural signature is extended with:

- board_portable_generation
- board_source_trading_date
- board_display_basis

A validated generation change therefore triggers the cache's normal one-shot
``structure_change`` rebuild. Until the cached signature matches the current
portable signature, stale cached rows are suppressed.
"""

from typing import Any

PATCH_VERSION = "portable_board_cache_sync_v2"
_SIGNATURE_HOOK_VERSION = "portable_generation_signature_v1"


def _portable_state_signature(state) -> tuple[int, str, str]:
    lock = getattr(state, "lock", None)
    if lock is None:
        status = getattr(state, "status", {}) or {}
        return (
            int(status.get("board_portable_generation") or 0),
            str(status.get("board_source_trading_date") or ""),
            str(status.get("board_display_basis") or ""),
        )
    with lock:
        status = getattr(state, "status", {}) or {}
        return (
            int(status.get("board_portable_generation") or 0),
            str(status.get("board_source_trading_date") or ""),
            str(status.get("board_display_basis") or ""),
        )


def _install_opening_signature_hook(opening) -> None:
    if getattr(opening, "_portable_generation_signature_installed", False):
        return

    original_display_version = opening._display_version

    def display_version_with_portable_generation(state):
        return (
            original_display_version(state),
            *_portable_state_signature(state),
        )

    opening._display_version = display_version_with_portable_generation
    opening._portable_generation_signature_installed = True
    opening._portable_generation_signature_version = _SIGNATURE_HOOK_VERSION


def _cache_portable_signature(state) -> tuple[int, str, str]:
    lock = getattr(state, "_opening_burst_cache_lock", None)
    if lock is None:
        signature = getattr(state, "_opening_burst_cache_signature", None)
    else:
        with lock:
            signature = getattr(state, "_opening_burst_cache_signature", None)

    if not isinstance(signature, tuple) or not signature:
        return 0, "", ""
    component = signature[-1]
    if not isinstance(component, tuple) or len(component) < 4:
        return 0, "", ""
    try:
        generation = int(component[1] or 0)
    except (TypeError, ValueError):
        generation = 0
    return generation, str(component[2] or ""), str(component[3] or "")


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

        expected_generation, expected_date, expected_basis = (
            _portable_state_signature(self)
        )

        # The opening-burst snapshot now sees the generation-aware structural
        # signature and schedules its own one-shot rebuild when it differs.
        payload = original_snapshot(self, limit)
        status = _status_dict(payload)
        cached_generation, cached_date, cached_basis = _cache_portable_signature(
            self
        )

        status.update(
            {
                "portable_board_cache_sync_version": PATCH_VERSION,
                "portable_board_cache_generation": expected_generation,
                "portable_board_cache_source_trading_date": expected_date
                or None,
                "portable_board_cache_basis": expected_basis or None,
                "portable_board_cache_ready_generation": cached_generation,
                "opening_burst_cache_portable_generation": cached_generation,
                "opening_burst_cache_portable_source_trading_date": cached_date
                or None,
                "opening_burst_cache_portable_basis": cached_basis or None,
            }
        )

        if not allowed or expected_basis.startswith("blocked_"):
            status["portable_board_cache_sync_status"] = (
                "blocked_waiting_valid_snapshot"
            )
            with self.lock:
                self.status.update(status)
            return _empty_rows(payload)

        if expected_basis == "portable_exact_close" and expected_generation > 0:
            ready = (
                cached_generation == expected_generation
                and cached_date == expected_date
                and cached_basis == expected_basis
            )
            status["portable_board_cache_sync_status"] = (
                "ready" if ready else "waiting_for_generation_rebuild"
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
    """Extend the later opening-cache install without changing production order."""
    import realtime_v2.worker_opening_burst_cache_patch as opening

    _install_opening_signature_hook(opening)

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
