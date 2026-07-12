from __future__ import annotations

from copy import deepcopy
from typing import Any


_POSITIVE_KEYS = {
    "bid_ask_ratio",
    "regular_close_bid_ask_ratio",
    "last_valid_bid_ask_ratio",
    "strength_1m",
    "one_min_strength",
    "regular_close_strength_1m",
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "execution_strength",
    "last_valid_strength_1m",
    "last_valid_strength_5m",
    "last_valid_execution_strength",
    "bid_pct",
    "ask_pct",
    "bid_volume",
    "ask_volume",
    "regular_close_bid_pct",
    "regular_close_ask_pct",
    "regular_close_bid_volume",
    "regular_close_ask_volume",
    "last_valid_bid_pct",
    "last_valid_ask_pct",
    "last_valid_bid_volume",
    "last_valid_ask_volume",
    "large_trade_buy_count",
    "large_trade_sell_count",
    "large_trade_buy_sum_eok",
    "large_trade_sell_sum_eok",
}
_NONZERO_KEYS = {
    "large_trade_net_count",
    "large_trade_net_sum_eok",
}


def _missing(actual_module: Any, key: str, value: Any) -> bool:
    if value is None or value == "":
        return True
    if key in _POSITIVE_KEYS:
        if isinstance(value, (int, float)):
            return float(value) <= 0
        number = actual_module.to_number(value)
        return number is None or float(number) <= 0
    if key in _NONZERO_KEYS:
        if isinstance(value, (int, float)):
            return float(value) == 0
        number = actual_module.to_number(value)
        return number is None or float(number) == 0
    return False


def _validated_by_code(actual_module: Any, state: Any) -> dict[str, tuple[tuple[str, Any], ...]]:
    raw = actual_module._load_previous_daily_display_values_if_needed(state)
    token = (id(raw), len(raw))
    cached_token = getattr(state, "previous_daily_fast_source_token", None)
    cached = getattr(state, "previous_daily_fast_validated_by_code", None)
    if cached_token == token and isinstance(cached, dict):
        return cached

    result: dict[str, tuple[tuple[str, Any], ...]] = {}
    validation_count = 0
    for raw_code, values in raw.items():
        code = actual_module.normalize_code(raw_code)
        if not code or not isinstance(values, dict):
            continue
        items: list[tuple[str, Any]] = []
        for key in actual_module.DISPLAY_FALLBACK_KEYS:
            if key not in values:
                continue
            value = values.get(key)
            if actual_module._fallback_value_is_usable(key, value):
                items.append((key, value))
                validation_count += 1
        if items:
            result[code] = tuple(items)

    state.previous_daily_fast_source_token = token
    state.previous_daily_fast_validated_by_code = result
    status = getattr(state, "status", None)
    if isinstance(status, dict):
        status["previous_daily_fast_cache_count"] = len(result)
        status["previous_daily_fast_validation_count"] = validation_count
    return result


def install(actual_module: Any) -> None:
    if getattr(actual_module, "_stockboard_previous_daily_fast_installed", False):
        return

    def apply_previous_daily_display_fallback(
        state: Any,
        row: dict[str, Any],
        session: dict[str, Any] | None,
    ) -> None:
        if not actual_module._should_use_previous_daily_display(session):
            return
        code = actual_module.normalize_code(row.get("stock_code"))
        if not code:
            return
        items = _validated_by_code(actual_module, state).get(code)
        if not items:
            return

        applied = 0
        for key, value in items:
            if not _missing(actual_module, key, row.get(key)):
                continue
            row[key] = deepcopy(value) if isinstance(value, (dict, list, tuple, set)) else value
            applied += 1
        if applied:
            row["previous_daily_display_fallback"] = True
            row["previous_daily_display_fallback_count"] = applied

    actual_module._apply_previous_daily_display_fallback = apply_previous_daily_display_fallback
    actual_module._stockboard_previous_daily_fast_installed = True
