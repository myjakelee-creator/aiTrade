from __future__ import annotations

"""Serialize the low-frequency hold overlay with accepted trade updates.

The continuity overlay runs at most once per two seconds and touches only the
existing in-memory rows.  Holding the State RLock around the complete overlay
prevents a just-accepted current-day trade from being replaced by the previous
close between the live-code scan and row update.  No source, request, loop, or
render cadence is added.
"""

from typing import Any

PATCH_VERSION = "board_display_continuity_rlock_v1"


def install(base) -> None:
    from realtime_v2 import worker_board_display_continuity_patch as continuity

    if getattr(continuity, "_display_continuity_rlock_installed", False):
        return

    original_apply_payload = continuity._apply_payload

    def apply_payload_locked(
        guard_module,
        state,
        payload: dict[str, Any],
        **kwargs: Any,
    ):
        lock = getattr(state, "lock", None)
        if lock is None:
            return original_apply_payload(guard_module, state, payload, **kwargs)
        with lock:
            return original_apply_payload(guard_module, state, payload, **kwargs)

    continuity._apply_payload = apply_payload_locked
    continuity._display_continuity_rlock_installed = True
    continuity._display_continuity_rlock_version = PATCH_VERSION

    state_class = getattr(base, "State", None)
    if state_class is not None:
        state_class._stockboard_display_continuity_rlock_installed = True
        state_class._stockboard_display_continuity_rlock_version = PATCH_VERSION
