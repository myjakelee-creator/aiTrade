from __future__ import annotations

import os
import time
from copy import deepcopy
from typing import Any


_TOKEN_KEYS = (
    "received_at",
    "orderbook_received_at",
    "one_min_strength_updated_at",
    "program_net_updated_at",
    "large_trade_updated_at",
    "strength_snapshot_at",
    "regular_close_orderbook_at",
    "last_valid_orderbook_at",
    "last_valid_strength_at",
)


def _source_token(source: dict[str, Any]) -> tuple[Any, ...]:
    return (
        id(source),
        len(source),
        *(source.get(key) for key in _TOKEN_KEYS),
    )


def _entry_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    return (
        id(entry),
        len(entry),
        entry.get("updated_at"),
        entry.get("trading_date"),
        entry.get("received_at"),
        entry.get("orderbook_received_at"),
        entry.get("program_net_updated_at"),
        entry.get("large_trade_updated_at"),
    )


def _current_daily_entry(hold: Any, state: Any, code: str) -> dict[str, Any] | None:
    source = getattr(state, "daily_values_by_code", {}).get(code)
    if not isinstance(source, dict):
        return None

    cache = getattr(state, "display_hold_fast_current_entries", None)
    if not isinstance(cache, dict):
        cache = {}
        state.display_hold_fast_current_entries = cache

    token = _source_token(source)
    cached = cache.get(code)
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == token:
        return cached[1] if isinstance(cached[1], dict) else None

    entry = hold._entry_from_source(code, source, "current_daily_state")
    cache[code] = (token, entry)
    return entry


def _candidate_entries(hold: Any, state: Any, code: str, allow_previous: bool) -> list[dict[str, Any]]:
    code = hold.normalize_code(code)
    if not code:
        return []

    result: list[dict[str, Any]] = []
    current = _current_daily_entry(hold, state, code)
    if isinstance(current, dict):
        result.append(current)

    today = hold.trading_date_text()
    cache_entry = hold._ensure_cache(state).get(code)
    if isinstance(cache_entry, dict):
        if allow_previous or hold._entry_date(cache_entry) in {"", today}:
            result.append(cache_entry)

    if allow_previous:
        previous = hold._load_previous_daily_entries(state).get(code)
        if isinstance(previous, dict):
            result.append(previous)

    return result


def _fast_missing(hold: Any, key: str, value: Any) -> bool:
    if value is None or value == "":
        return True

    if key == "ohlc":
        if not isinstance(value, dict):
            return True
        for field in ("open", "high", "low", "close"):
            item = value.get(field)
            if isinstance(item, (int, float)):
                if float(item) <= 0:
                    return True
            elif hold._positive(item) is None:
                return True
        return False

    if key in hold.POSITIVE_NUMBER_KEYS or key == "large_trade_threshold_krw":
        if isinstance(value, (int, float)):
            return float(value) <= 0
        return hold._positive(value) is None

    if key in hold.PRESENT_NUMBER_KEYS:
        if isinstance(value, (int, float)):
            return False
        return hold._number(value) is None

    if key in hold.COUNTER_KEYS:
        if isinstance(value, (int, float)):
            return float(value) == 0
        number = hold._number(value)
        return number is None or number == 0

    return False


def _normalized_entry_value(hold: Any, key: str, value: Any) -> Any:
    if value is None or value == "":
        return None
    if key == "ohlc":
        return value if isinstance(value, dict) else hold._usable_value(key, value)
    if key in hold.POSITIVE_NUMBER_KEYS | hold.PRESENT_NUMBER_KEYS | hold.COUNTER_KEYS:
        if isinstance(value, (int, float)):
            return value
        return hold._usable_value(key, value)
    if key == "large_trade_threshold_krw":
        if isinstance(value, (int, float)):
            return int(value) if float(value) > 0 else None
        return hold._usable_value(key, value)
    return value


def _merged_entry(hold: Any, state: Any, code: str, allow_previous: bool) -> dict[str, Any]:
    entries = _candidate_entries(hold, state, code, allow_previous)
    signature = tuple(_entry_signature(entry) for entry in entries)

    cache = getattr(state, "display_hold_fast_merged_entries", None)
    if not isinstance(cache, dict):
        cache = {}
        state.display_hold_fast_merged_entries = cache

    cache_key = (code, bool(allow_previous))
    cached = cache.get(cache_key)
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == signature:
        return cached[1] if isinstance(cached[1], dict) else {}

    merged: dict[str, Any] = {}
    for key in hold.DISPLAY_HOLD_KEYS:
        for entry in entries:
            if key not in entry:
                continue
            value = _normalized_entry_value(hold, key, entry.get(key))
            if value is not None:
                merged[key] = value
                break

    cache[cache_key] = (signature, merged)
    return merged


def _apply_hold_to_row(hold: Any, state: Any, row: dict[str, Any], allow_previous: bool) -> int:
    code = hold.normalize_code(row.get("stock_code"))
    if not code:
        return 0

    merged = _merged_entry(hold, state, code, allow_previous)
    applied = 0
    for key, value in merged.items():
        if _fast_missing(hold, key, row.get(key)):
            row[key] = deepcopy(value) if isinstance(value, (dict, list, tuple, set)) else value
            applied += 1

    if not _fast_missing(hold, "price", row.get("price")) and _fast_missing(
        hold, "trade_price", row.get("trade_price")
    ):
        row["trade_price"] = row.get("price")
    if not _fast_missing(hold, "trade_price", row.get("trade_price")) and _fast_missing(
        hold, "price", row.get("price")
    ):
        row["price"] = row.get("trade_price")

    ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else None
    if ohlc and not _fast_missing(hold, "ohlc", ohlc):
        for source_key, target_key in (
            ("open", "day_open"),
            ("high", "day_high"),
            ("low", "day_low"),
            ("close", "day_close"),
        ):
            if _fast_missing(hold, target_key, row.get(target_key)):
                row[target_key] = ohlc.get(source_key)

    if applied:
        row["display_hold_applied"] = True
        row["display_hold_applied_count"] = applied
    return applied


def _invalidate_code(state: Any, code: str) -> None:
    current = getattr(state, "display_hold_fast_current_entries", None)
    if isinstance(current, dict):
        current.pop(code, None)
    merged = getattr(state, "display_hold_fast_merged_entries", None)
    if isinstance(merged, dict):
        merged.pop((code, False), None)
        merged.pop((code, True), None)


def _remember_from_quote(hold: Any, state: Any, code: str, quote: dict[str, Any], origin: str) -> None:
    code = hold.normalize_code(code)
    if not code or not isinstance(quote, dict):
        return

    interval = max(0.1, float(os.getenv("STOCKBOARD_DISPLAY_HOLD_CAPTURE_SEC", "1.0")))
    now_mono = time.monotonic()
    last_by_code = getattr(state, "display_hold_fast_last_capture", None)
    if not isinstance(last_by_code, dict):
        last_by_code = {}
        state.display_hold_fast_last_capture = last_by_code

    if origin != "close_metrics" and now_mono - float(last_by_code.get(code, 0.0) or 0.0) < interval:
        state.status["display_hold_capture_skip_count"] = int(
            state.status.get("display_hold_capture_skip_count") or 0
        ) + 1
        return

    entry = hold._entry_from_source(code, quote, origin)
    if entry is None:
        return

    last_by_code[code] = now_mono
    cache = hold._ensure_cache(state)
    cache[code] = entry
    state.display_hold_dirty = True
    state.status["display_hold_last_code"] = code
    state.status["display_hold_last_at"] = entry.get("updated_at") or hold.now_text()
    state.status["display_hold_cache_count"] = len(cache)
    state.status["display_hold_capture_count"] = int(
        state.status.get("display_hold_capture_count") or 0
    ) + 1

    daily = state.daily_values_by_code.setdefault(code, {})
    for key, value in entry.items():
        if key == "stock_code" or value is None or value == "":
            continue
        daily[key] = deepcopy(value) if isinstance(value, (dict, list, tuple, set)) else value
    if hasattr(state, "_mark_daily_dirty"):
        state._mark_daily_dirty()
    _invalidate_code(state, code)


def _write_cache_if_needed(hold: Any, state: Any, force: bool = False) -> None:
    now_mono = time.monotonic()
    lock = getattr(state, "lock", None)

    def prepare() -> tuple[bool, dict[str, dict[str, Any]]]:
        if not force and not getattr(state, "display_hold_dirty", False):
            return False, {}
        if not force and now_mono - float(getattr(state, "display_hold_last_save", 0.0) or 0.0) < hold.DISPLAY_HOLD_SAVE_INTERVAL_SEC:
            return False, {}
        cache = getattr(state, "display_hold_by_code", {}) or {}
        values = {
            code: dict(entry)
            for code, entry in sorted(cache.items())
            if isinstance(entry, dict)
        }
        state.display_hold_dirty = False
        state.display_hold_last_save = now_mono
        return True, values

    if lock is None:
        ready, values = prepare()
    else:
        with lock:
            ready, values = prepare()
    if not ready:
        return

    try:
        hold.atomic_write_json(
            hold.DISPLAY_HOLD_PATH,
            {
                "schema_version": 1,
                "source": "stockboard_v2_display_hold_last_valid",
                "trading_date": hold.trading_date_text(),
                "ts": hold.now_text(),
                "count": len(values),
                "values": values,
            },
        )
    except Exception as error:
        if lock is None:
            state.display_hold_dirty = True
            state.status["display_hold_save_error"] = f"{type(error).__name__}: {error}"
        else:
            with lock:
                state.display_hold_dirty = True
                state.status["display_hold_save_error"] = f"{type(error).__name__}: {error}"
        return

    def publish() -> None:
        state.status["display_hold_cache_count"] = len(values)
        state.status["display_hold_saved_at"] = hold.now_text()
        state.status["display_hold_path"] = str(hold.DISPLAY_HOLD_PATH)
        state.status["display_hold_save_error"] = None

    if lock is None:
        publish()
    else:
        with lock:
            publish()


def install(hold_module: Any) -> None:
    """Reduce display-hold row scans and remove file I/O from event callbacks."""

    if getattr(hold_module, "_stockboard_display_hold_fast_installed", False):
        return

    hold_module._candidate_entries = lambda state, code, allow_previous: _candidate_entries(
        hold_module, state, code, allow_previous
    )
    hold_module._apply_hold_to_row = lambda state, row, allow_previous: _apply_hold_to_row(
        hold_module, state, row, allow_previous
    )
    hold_module._remember_from_quote = lambda state, code, quote, origin: _remember_from_quote(
        hold_module, state, code, quote, origin
    )
    hold_module._write_cache_if_needed = lambda state, force=False: _write_cache_if_needed(
        hold_module, state, force
    )
    hold_module._stockboard_display_hold_fast_installed = True
