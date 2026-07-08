from __future__ import annotations

import builtins
from copy import deepcopy
from typing import Any


def _stockboard_restore_persisted_live_metrics(quote: dict[str, Any], persisted: dict[str, Any]) -> None:
    """Runtime fallback for realtime_v2.worker64_guarded helper lookups.

    Python resolves missing module globals through builtins.  This keeps the
    guarded worker from failing if a helper is accidentally omitted during a hot
    patch, without adding work to the realtime path beyond a few dictionary
    copies during quote creation.
    """
    keys = (
        "execution_strength",
        "execution_strength_updated_at",
        "ask_volume",
        "bid_volume",
        "ask_pct",
        "bid_pct",
        "bid_ask_ratio",
        "best_ask_price",
        "best_bid_price",
        "orderbook_received_at",
        "day_open",
        "day_high",
        "day_low",
        "day_close",
        "ohlc",
        "strength_1m",
        "one_min_buy_qty",
        "one_min_sell_qty",
        "one_min_strength_updated_at",
    )
    if not isinstance(quote, dict) or not isinstance(persisted, dict):
        return
    for key in keys:
        if key in persisted:
            quote[key] = deepcopy(persisted.get(key))


def _stockboard_persist_live_metrics(state: Any, code: str, quote: dict[str, Any], *keys: str) -> None:
    if not code or not isinstance(quote, dict):
        return
    entry = state.daily_values_by_code.setdefault(code, {})
    changed = False
    for key in keys:
        value = quote.get(key)
        if value not in (None, "") and entry.get(key) != value:
            entry[key] = deepcopy(value)
            changed = True
    if changed and hasattr(state, "_mark_daily_dirty"):
        state._mark_daily_dirty()


# Only install fallbacks.  If a module defines its own globals, those globals win.
if not hasattr(builtins, "_restore_persisted_live_metrics"):
    builtins._restore_persisted_live_metrics = _stockboard_restore_persisted_live_metrics
if not hasattr(builtins, "_persist_live_metrics"):
    builtins._persist_live_metrics = _stockboard_persist_live_metrics
