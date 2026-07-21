from __future__ import annotations

"""Remove filesystem and fingerprint work from the active-session SSE path.

The display-continuity guard needs the previous verified exact-close payload during
premarket/regular trading, but that payload is immutable for the whole trading day.
Resolve it once per previous trading date, keep it in memory, and reuse it for every
snapshot request.  No QAx, FID, REST, WebSocket, thread, timer, or SSE cadence is
added or changed.
"""

import time
from datetime import datetime
from typing import Any

from realtime_v2.market_session import last_completed_trading_date

PATCH_VERSION = "board_display_continuity_active_cache_v1"
PAYLOAD_RETRY_SEC = 5.0


def _ensure_active_cache(guard) -> None:
    if getattr(guard, "_display_active_cache_ready", False):
        return
    guard._display_active_cache_ready = True
    guard._display_active_payload_date = ""
    guard._display_active_payload = None
    guard._display_active_fingerprint = ""
    guard._display_active_generation = 0
    guard._display_active_next_retry_mono = 0.0
    guard._display_active_lookup_count = 0
    guard._display_active_file_read_count = 0
    guard._display_active_hit_count = 0
    guard._display_active_last_lookup_ms = None


def _active_payload(continuity, guard_module, guard, previous_date: str):
    """Return one verified previous-close payload without repeated file I/O."""

    _ensure_active_cache(guard)
    previous_date = continuity._date_digits(previous_date)
    cached = getattr(guard, "_display_active_payload", None)
    if (
        previous_date
        and getattr(guard, "_display_active_payload_date", "") == previous_date
        and continuity._trusted_payload(
            guard_module,
            cached,
            expected_date=previous_date,
            require_verified=True,
        )
    ):
        guard._display_active_hit_count += 1
        return (
            cached,
            str(getattr(guard, "_display_active_fingerprint", "") or ""),
            int(getattr(guard, "_display_active_generation", 0) or 0),
            "hit",
        )

    now_mono = time.monotonic()
    if now_mono < float(getattr(guard, "_display_active_next_retry_mono", 0.0) or 0.0):
        return None, "", int(getattr(guard, "_display_active_generation", 0) or 0), "retry_wait"

    started = time.perf_counter()
    guard._display_active_lookup_count += 1
    candidates: list[Any] = [getattr(guard, "_display_last_good_payload", None)]

    # Disk candidates are read only on a cache miss, normally once per trading day.
    for path in (
        getattr(guard, "_display_last_good_path", None),
        getattr(guard, "_display_candidate_path", None),
    ):
        if path is None:
            continue
        guard._display_active_file_read_count += 1
        candidates.append(continuity._read_json(path))

    try:
        guard._display_active_file_read_count += 1
        candidates.append(guard._load(force=True))
    except Exception:
        candidates.append(None)

    payload = next(
        (
            item
            for item in candidates
            if continuity._trusted_payload(
                guard_module,
                item,
                expected_date=previous_date,
                require_verified=True,
            )
        ),
        None,
    )
    guard._display_active_last_lookup_ms = round(
        (time.perf_counter() - started) * 1000.0,
        3,
    )

    if not isinstance(payload, dict):
        guard._display_active_payload_date = previous_date
        guard._display_active_payload = None
        guard._display_active_next_retry_mono = now_mono + PAYLOAD_RETRY_SEC
        return None, "", int(getattr(guard, "_display_active_generation", 0) or 0), "miss"

    fingerprint, generation = continuity._remember_verified(
        guard_module,
        guard,
        payload,
    )
    # _remember_verified owns the defensive deepcopy; keep that in-memory object.
    stable_payload = getattr(guard, "_display_last_good_payload", None)
    if not isinstance(stable_payload, dict):
        stable_payload = payload
    guard._display_active_payload_date = previous_date
    guard._display_active_payload = stable_payload
    guard._display_active_fingerprint = fingerprint
    guard._display_active_generation = generation
    guard._display_active_next_retry_mono = 0.0
    return stable_payload, fingerprint, generation, "loaded"


def _publish_runtime_status(state, guard, cache_status: str, apply_ms: float) -> None:
    with state.lock:
        state.status.update(
            {
                "board_display_continuity_runtime_opt_version": PATCH_VERSION,
                "board_display_active_payload_cache_status": cache_status,
                "board_display_active_payload_date": getattr(
                    guard, "_display_active_payload_date", None
                )
                or None,
                "board_display_active_payload_lookup_count": int(
                    getattr(guard, "_display_active_lookup_count", 0) or 0
                ),
                "board_display_active_payload_file_read_count": int(
                    getattr(guard, "_display_active_file_read_count", 0) or 0
                ),
                "board_display_active_payload_hit_count": int(
                    getattr(guard, "_display_active_hit_count", 0) or 0
                ),
                "board_display_active_payload_last_lookup_ms": getattr(
                    guard, "_display_active_last_lookup_ms", None
                ),
                "board_display_active_apply_ms": round(float(apply_ms), 3),
            }
        )


def install(base) -> None:
    from realtime_v2 import worker_board_display_continuity_patch as continuity
    from realtime_v2 import worker_board_trading_date_guard as guard_module

    guard_class = guard_module.PortableBoardGuard
    if getattr(guard_class, "_stockboard_display_continuity_runtime_opt_installed", False):
        return

    original_apply = guard_class.apply

    def apply(self, state, now=None):
        started = time.perf_counter()
        current = now or datetime.now()
        target_date, phase, active = guard_module.board_target_context(current)
        if not active:
            result = original_apply(self, state, current)
            _publish_runtime_status(
                state,
                self,
                "inactive_passthrough",
                (time.perf_counter() - started) * 1000.0,
            )
            return result

        previous_date = continuity._date_digits(last_completed_trading_date(current))
        payload, fingerprint, generation, cache_status = _active_payload(
            continuity,
            guard_module,
            self,
            previous_date,
        )
        if not isinstance(payload, dict):
            self._status(
                state,
                target_date=target_date,
                phase=phase,
                basis="live_session_passthrough",
                payload=None,
                generation=int(state.status.get("board_portable_generation") or 0),
            )
            continuity._set_continuity_status(
                state,
                mode="live_passthrough_no_previous_exact",
                current_date=target_date,
                source_date=None,
                fingerprint=None,
                generation=int(state.status.get("board_portable_generation") or 0),
                applied=0,
                held=0,
                live=0,
            )
            self._applied_result = True
            _publish_runtime_status(
                state,
                self,
                cache_status,
                (time.perf_counter() - started) * 1000.0,
            )
            return True

        overlay_key = (target_date, fingerprint)
        now_mono = time.monotonic()
        if (
            getattr(self, "_display_last_overlay_key", None) != overlay_key
            or now_mono - float(getattr(self, "_display_last_overlay_mono", 0.0) or 0.0)
            >= continuity.OVERLAY_INTERVAL_SEC
        ):
            applied, held, live = continuity._apply_payload(
                guard_module,
                state,
                payload,
                source_date=previous_date,
                current_date=target_date,
                hold_only=True,
                generation=generation,
            )
            self._display_last_overlay_key = overlay_key
            self._display_last_overlay_mono = now_mono
            self._display_last_counts = (applied, held, live)
        else:
            applied, held, live = getattr(self, "_display_last_counts", (0, 0, 0))

        self._status(
            state,
            target_date=target_date,
            phase=phase,
            basis="portable_exact_close",
            payload=payload,
            generation=generation,
            exact_count=applied,
            missing_count=0,
        )
        continuity._set_continuity_status(
            state,
            mode="live_with_previous_close_hold" if held else "live_current_day_ready",
            current_date=target_date,
            source_date=previous_date,
            fingerprint=fingerprint,
            generation=generation,
            applied=applied,
            held=held,
            live=live,
        )
        self._applied_result = True
        _publish_runtime_status(
            state,
            self,
            cache_status,
            (time.perf_counter() - started) * 1000.0,
        )
        return True

    guard_class.apply = apply
    guard_class._stockboard_display_continuity_runtime_opt_installed = True
    guard_class._stockboard_display_continuity_runtime_opt_version = PATCH_VERSION

    state_class = getattr(base, "State", None)
    if state_class is not None:
        state_class._stockboard_display_continuity_runtime_opt_installed = True
        state_class._stockboard_display_continuity_runtime_opt_version = PATCH_VERSION
