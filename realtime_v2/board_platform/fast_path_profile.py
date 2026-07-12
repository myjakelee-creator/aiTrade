from __future__ import annotations

import functools
import os
import threading
import time
from copy import deepcopy
from typing import Any, Callable


_STAGE_KEYS = (
    "quote_ensure",
    "deepcopy",
    "session",
    "ohlc_load",
    "strength_load",
    "event_age",
    "copy_previous",
    "previous_daily",
    "strength_policy",
    "orderbook_policy",
    "afterclose_restore",
    "model_merge",
    "display_order",
)
_HELPER_STAGES = {
    "_update_market_session_status": "session",
    "_load_ohlc_snapshot_if_needed": "ohlc_load",
    "_load_strength_snapshot_if_needed": "strength_load",
    "event_age_sec": "event_age",
    "_copy_previous_fields": "copy_previous",
    "_apply_previous_daily_display_fallback": "previous_daily",
    "_apply_aftermarket_strength_display_policy": "strength_policy",
    "_apply_aftermarket_orderbook_display_policy": "orderbook_policy",
    "_restore_afterclose_metric_display": "afterclose_restore",
}

_local = threading.local()
_profile_lock = threading.RLock()
_last_profile: dict[str, Any] = {}
_rows_call_count = 0
_sample_count = 0


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(str(os.getenv(name, default)).strip()))
    except (TypeError, ValueError):
        return max(1, int(default))


def _active_context() -> dict[str, Any] | None:
    value = getattr(_local, "context", None)
    return value if isinstance(value, dict) else None


def _record(stage: str, elapsed_ms: float) -> None:
    context = _active_context()
    if context is None:
        return
    context[f"{stage}_ms"] = context.get(f"{stage}_ms", 0.0) + elapsed_ms
    context[f"{stage}_calls"] = int(context.get(f"{stage}_calls", 0)) + 1


def _timed(stage: str, function: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(function, "_stockboard_fast_profile_wrapped", False):
        return function

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        context = _active_context()
        if context is None:
            return function(*args, **kwargs)
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            _record(stage, (time.perf_counter() - started) * 1000.0)

    wrapped._stockboard_fast_profile_wrapped = True
    wrapped._stockboard_fast_profile_original = function
    return wrapped


def _round(value: Any) -> float:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return 0.0


def _publish_state_profile(state: Any, profile: dict[str, Any]) -> None:
    status = getattr(state, "status", None)
    if not isinstance(status, dict):
        return
    lock = getattr(state, "lock", None)
    if lock is None:
        status.update(profile)
        return
    try:
        with lock:
            status.update(profile)
    except Exception:
        status.update(profile)


def _profile_payload(prefix: str, profile: dict[str, Any]) -> dict[str, Any]:
    result = {
        f"{prefix}_sample_count": profile.get("sample_count"),
        f"{prefix}_sample_every": profile.get("sample_every"),
        f"{prefix}_row_count": profile.get("row_count"),
        f"{prefix}_rows_total_ms": profile.get("rows_total_ms"),
        f"{prefix}_snapshot_ms": profile.get("snapshot_ms"),
        f"{prefix}_unaccounted_ms": profile.get("unaccounted_ms"),
        f"{prefix}_profile_at": profile.get("profile_at"),
    }
    for stage in _STAGE_KEYS:
        result[f"{prefix}_{stage}_ms"] = profile.get(f"{stage}_ms")
        result[f"{prefix}_{stage}_calls"] = profile.get(f"{stage}_calls")
    return result


def install(actual_module: Any, base_module: Any) -> None:
    global _rows_call_count
    global _sample_count

    if getattr(actual_module, "_stockboard_fast_path_profile_installed", False):
        return

    sample_every = _env_int("STOCKBOARD_FAST_PROFILE_EVERY", 5)

    for function_name, stage in _HELPER_STAGES.items():
        function = getattr(actual_module, function_name, None)
        if callable(function):
            setattr(actual_module, function_name, _timed(stage, function))

    module_deepcopy = getattr(actual_module, "deepcopy", None)
    if callable(module_deepcopy):
        actual_module.deepcopy = _timed("deepcopy", module_deepcopy)

    state_class = base_module.State
    quote_method = getattr(state_class, "_quote", None)
    if callable(quote_method):
        state_class._quote = _timed("quote_ensure", quote_method)

    display_controller = getattr(actual_module, "DisplayOrderController", None)
    if display_controller is not None:
        apply_method = getattr(display_controller, "apply", None)
        if callable(apply_method):
            display_controller.apply = _timed("display_order", apply_method)

    try:
        from realtime_v2.board_platform.model_lane import StockBoardModelLaneService

        apply_method = getattr(StockBoardModelLaneService, "apply", None)
        if callable(apply_method):
            StockBoardModelLaneService.apply = _timed("model_merge", apply_method)
    except Exception:
        pass

    original_rows = state_class.rows

    @functools.wraps(original_rows)
    def profiled_rows(self, limit: int = 300):
        global _rows_call_count
        global _sample_count

        with _profile_lock:
            _rows_call_count += 1
            should_sample = _rows_call_count % sample_every == 0
        if not should_sample or _active_context() is not None:
            return original_rows(self, limit)

        context: dict[str, Any] = {
            "started_at": time.perf_counter(),
            "sample_every": sample_every,
        }
        for stage in _STAGE_KEYS:
            context[f"{stage}_ms"] = 0.0
            context[f"{stage}_calls"] = 0

        _local.context = context
        rows = None
        try:
            rows = original_rows(self, limit)
            return rows
        finally:
            rows_total_ms = (time.perf_counter() - context["started_at"]) * 1000.0
            _local.context = None
            accounted_ms = sum(float(context.get(f"{stage}_ms") or 0.0) for stage in _STAGE_KEYS)
            with _profile_lock:
                _sample_count += 1
                profile = {
                    "sample_count": _sample_count,
                    "sample_every": sample_every,
                    "row_count": len(rows) if isinstance(rows, list) else None,
                    "rows_total_ms": _round(rows_total_ms),
                    "unaccounted_ms": _round(max(0.0, rows_total_ms - accounted_ms)),
                    "profile_at": time.time(),
                }
                for stage in _STAGE_KEYS:
                    profile[f"{stage}_ms"] = _round(context.get(f"{stage}_ms"))
                    profile[f"{stage}_calls"] = int(context.get(f"{stage}_calls") or 0)
                _last_profile.clear()
                _last_profile.update(profile)
            _publish_state_profile(self, _profile_payload("fast_profile", profile))

    state_class.rows = profiled_rows

    original_snapshot = state_class.snapshot

    @functools.wraps(original_snapshot)
    def profiled_snapshot(self, limit: int = 300):
        started = time.perf_counter()
        payload = original_snapshot(self, limit)
        snapshot_ms = (time.perf_counter() - started) * 1000.0
        with _profile_lock:
            profile = deepcopy(_last_profile)
            profile["snapshot_ms"] = _round(snapshot_ms)
            if profile:
                _last_profile["snapshot_ms"] = profile["snapshot_ms"]
        if profile:
            flat = _profile_payload("fast_profile", profile)
            _publish_state_profile(self, flat)
            if isinstance(payload, dict):
                payload_status = dict(payload.get("status") or {})
                payload_status.update(flat)
                payload["status"] = payload_status
        return payload

    state_class.snapshot = profiled_snapshot

    from realtime_v2.board_platform.stockboard_cache import StockBoardSnapshotCacheService

    original_cache_status = StockBoardSnapshotCacheService.status

    @functools.wraps(original_cache_status)
    def profiled_cache_status(self):
        result = original_cache_status(self)
        state_status = getattr(self.state, "status", {}) or {}
        for key, value in state_status.items():
            if str(key).startswith("fast_profile_"):
                result[key] = value
        result["fast_profile_enabled"] = True
        return result

    StockBoardSnapshotCacheService.status = profiled_cache_status
    actual_module._stockboard_fast_path_profile_installed = True
