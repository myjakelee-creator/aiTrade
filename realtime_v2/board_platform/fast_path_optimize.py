from __future__ import annotations

import functools
from typing import Any


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
    """Avoid repeated static enrichment for already-initialized quotes.

    The guarded quote initializer applies previous-day values plus cached OHLC and
    strength data. Those values are static between snapshot-file changes, so doing
    the same work for every trade, orderbook event, and board snapshot only holds
    the worker lock longer. Existing quotes now return immediately. New quotes keep
    the original complete initialization path. When an OHLC/strength file mtime
    changes, the new snapshot is bulk-applied to all existing quotes exactly once.
    """

    if getattr(actual_module, "_stockboard_fast_path_optimize_installed", False):
        return

    state_class = base_module.State
    original_quote = state_class._quote

    @functools.wraps(original_quote)
    def optimized_quote(self, code):
        normalized = actual_module.normalize_code(code)
        existing = getattr(self, "quotes", {}).get(normalized)
        if isinstance(existing, dict):
            return existing
        return original_quote(self, code)

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
    actual_module._load_ohlc_snapshot_if_needed = optimized_ohlc_loader
    actual_module._load_strength_snapshot_if_needed = optimized_strength_loader
    actual_module._stockboard_fast_path_optimize_installed = True
