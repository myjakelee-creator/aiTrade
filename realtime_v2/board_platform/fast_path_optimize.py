from __future__ import annotations

import functools
import threading
from typing import Any


_local = threading.local()


def _in_snapshot_rows() -> bool:
    return bool(getattr(_local, "snapshot_rows_depth", 0))


def _enter_snapshot_rows() -> None:
    _local.snapshot_rows_depth = int(getattr(_local, "snapshot_rows_depth", 0)) + 1


def _leave_snapshot_rows() -> None:
    depth = max(0, int(getattr(_local, "snapshot_rows_depth", 1)) - 1)
    _local.snapshot_rows_depth = depth


def _bulk_apply_ohlc(actual_module: Any, state: Any) -> int:
    applied = 0
    for code, quote in list(getattr(state, "quotes", {}).items()):
        if not isinstance(quote, dict):
            continue
        before = quote.get("ohlc_snapshot_applied_at")
        actual_module._apply_ohlc_snapshot_to_quote(state, code, quote)
        if quote.get("ohlc_snapshot_applied_at") != before:
            applied += 1
    return applied


def _bulk_apply_strength(actual_module: Any, state: Any) -> int:
    applied = 0
    for code, quote in list(getattr(state, "quotes", {}).items()):
        if not isinstance(quote, dict):
            continue
        snapshot = getattr(state, "strength_snapshot_by_code", {}).get(code)
        if not isinstance(snapshot, dict):
            continue
        before = tuple(
            quote.get(key)
            for key in (
                "strength_5m",
                "strength_20m",
                "strength_60m",
                "strength_source",
                "strength_snapshot_at",
                "strength_status",
            )
        )
        actual_module._apply_strength_snapshot_to_quote(state, code, quote)
        after = tuple(
            quote.get(key)
            for key in (
                "strength_5m",
                "strength_20m",
                "strength_60m",
                "strength_source",
                "strength_snapshot_at",
                "strength_status",
            )
        )
        if after != before:
            applied += 1
    return applied


def install(actual_module: Any, base_module: Any) -> None:
    """Remove repeated quote enrichment from the fast snapshot path.

    Universe load and event handlers still call the original guarded _quote().
    During State.rows() only, already-existing quotes are returned directly;
    missing quotes still use the original initializer. OHLC/strength snapshot
    changes are applied to all existing quotes once when the source mtime changes.
    """

    if getattr(actual_module, "_stockboard_fast_path_optimize_installed", False):
        return

    state_class = base_module.State
    original_quote = state_class._quote
    original_rows = state_class.rows

    @functools.wraps(original_quote)
    def optimized_quote(self, code):
        normalized = actual_module.normalize_code(code)
        if _in_snapshot_rows():
            existing = getattr(self, "quotes", {}).get(normalized)
            if isinstance(existing, dict):
                status = getattr(self, "status", None)
                if isinstance(status, dict):
                    status["fast_quote_existing_skip_count"] = int(
                        status.get("fast_quote_existing_skip_count") or 0
                    ) + 1
                return existing
        return original_quote(self, code)

    @functools.wraps(original_rows)
    def optimized_rows(self, limit: int = 300):
        _enter_snapshot_rows()
        try:
            return original_rows(self, limit)
        finally:
            _leave_snapshot_rows()

    original_ohlc_loader = actual_module._load_ohlc_snapshot_if_needed

    @functools.wraps(original_ohlc_loader)
    def optimized_ohlc_loader(state, force: bool = False):
        before = getattr(state, "ohlc_snapshot_mtime", None)
        result = original_ohlc_loader(state, force=force)
        after = getattr(state, "ohlc_snapshot_mtime", None)
        if after is not None and after != before:
            applied = _bulk_apply_ohlc(actual_module, state)
            status = getattr(state, "status", None)
            if isinstance(status, dict):
                status["fast_ohlc_bulk_apply_count"] = int(
                    status.get("fast_ohlc_bulk_apply_count") or 0
                ) + applied
                status["fast_ohlc_bulk_apply_last"] = applied
        return result

    original_strength_loader = actual_module._load_strength_snapshot_if_needed

    @functools.wraps(original_strength_loader)
    def optimized_strength_loader(state, force: bool = False):
        before = getattr(state, "strength_snapshot_mtime", None)
        result = original_strength_loader(state, force=force)
        after = getattr(state, "strength_snapshot_mtime", None)
        if after is not None and after != before:
            applied = _bulk_apply_strength(actual_module, state)
            status = getattr(state, "status", None)
            if isinstance(status, dict):
                status["fast_strength_bulk_apply_count"] = int(
                    status.get("fast_strength_bulk_apply_count") or 0
                ) + applied
                status["fast_strength_bulk_apply_last"] = applied
        return result

    state_class._quote = optimized_quote
    state_class.rows = optimized_rows
    actual_module._load_ohlc_snapshot_if_needed = optimized_ohlc_loader
    actual_module._load_strength_snapshot_if_needed = optimized_strength_loader
    actual_module._stockboard_fast_path_optimize_installed = True
