from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

from realtime_v2.common import (
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    now_text,
    to_number,
    trading_date_text,
)

SNAPSHOT_PATH = RUNTIME_DIR / "bid_ask_ratio_last.json"
CACHE_CHECK_INTERVAL_SEC = 5.0
CACHE_SAVE_INTERVAL_SEC = 5.0
POSITIVE_RATIO_KEYS = (
    "bid_ask_ratio",
    "bid_ask_ratio_snapshot",
    "last_valid_bid_ask_ratio",
    "regular_close_bid_ask_ratio",
)
BID_VOLUME_KEYS = (
    "bid_volume",
    "bid_volume_snapshot",
    "last_valid_bid_volume",
    "regular_close_bid_volume",
)
ASK_VOLUME_KEYS = (
    "ask_volume",
    "ask_volume_snapshot",
    "last_valid_ask_volume",
    "regular_close_ask_volume",
)
BID_PCT_KEYS = ("bid_pct", "last_valid_bid_pct", "regular_close_bid_pct")
ASK_PCT_KEYS = ("ask_pct", "last_valid_ask_pct", "regular_close_ask_pct")
AT_KEYS = (
    "orderbook_snapshot_at",
    "orderbook_completed_at",
    "orderbook_received_at",
    "last_valid_orderbook_at",
    "regular_close_orderbook_at",
    "updated_at",
)


def _positive_number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    number = float(number)
    if number <= 0:
        return None
    return number


def _first_positive(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _positive_number(source.get(key))
        if value is not None:
            return value
    return None


def _first_value(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _entry_from_values(code: str, values: dict[str, Any], *, source: str = "unknown") -> dict[str, Any] | None:
    code = normalize_code(code)
    if not code or not isinstance(values, dict):
        return None

    ratio = _first_positive(values, POSITIVE_RATIO_KEYS)
    bid_volume = _first_positive(values, BID_VOLUME_KEYS)
    ask_volume = _first_positive(values, ASK_VOLUME_KEYS)

    if ratio is None and bid_volume is not None and ask_volume is not None:
        if ask_volume > 0:
            ratio = bid_volume / ask_volume
        elif bid_volume > 0:
            ratio = 20.0

    if ratio is None:
        return None

    ratio = round(max(0.01, min(20.0, float(ratio))), 4)
    bid_pct = _first_positive(values, BID_PCT_KEYS)
    ask_pct = _first_positive(values, ASK_PCT_KEYS)
    if (bid_pct is None or ask_pct is None) and bid_volume is not None and ask_volume is not None:
        total = bid_volume + ask_volume
        if total > 0:
            bid_pct = round(bid_volume / total * 100)
            ask_pct = 100 - bid_pct

    updated_at = _first_value(values, AT_KEYS) or now_text()
    entry: dict[str, Any] = {
        "stock_code": code,
        "trading_date": trading_date_text(),
        "bid_ask_ratio": ratio,
        "last_valid_bid_ask_ratio": ratio,
        "last_valid_orderbook_at": updated_at,
        "orderbook_display_basis": "last_valid_bidask_cache",
        "orderbook_source": values.get("orderbook_source") or source,
        "updated_at": now_text(),
    }
    if bid_pct is not None:
        entry["bid_pct"] = round(float(bid_pct), 4)
        entry["last_valid_bid_pct"] = entry["bid_pct"]
    if ask_pct is not None:
        entry["ask_pct"] = round(float(ask_pct), 4)
        entry["last_valid_ask_pct"] = entry["ask_pct"]
    if bid_volume is not None:
        entry["bid_volume"] = int(float(bid_volume))
        entry["last_valid_bid_volume"] = entry["bid_volume"]
    if ask_volume is not None:
        entry["ask_volume"] = int(float(ask_volume))
        entry["last_valid_ask_volume"] = entry["ask_volume"]
    return entry


def _load_snapshot_file() -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    payload_date = "".join(ch for ch in str(payload.get("trading_date") or "") if ch.isdigit())[:8]
    today = trading_date_text()
    if payload_date and payload_date != today:
        return {}
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_code, raw_entry in raw_values.items():
        code = normalize_code(raw_code)
        if not code or not isinstance(raw_entry, dict):
            continue
        entry = _entry_from_values(code, raw_entry, source="bidask_snapshot_file")
        if entry is not None:
            result[code] = entry
    return result


def _ensure_cache(state, *, force: bool = False) -> dict[str, dict[str, Any]]:
    now_mono = time.monotonic()
    if not hasattr(state, "bidask_last_cache_by_code"):
        state.bidask_last_cache_by_code = {}
        state.bidask_last_cache_loaded_at = 0.0
        state.bidask_last_cache_mtime = None
        state.bidask_last_cache_dirty = False
        state.bidask_last_cache_saved_at_mono = 0.0
    last_check = float(getattr(state, "bidask_last_cache_loaded_at", 0.0) or 0.0)
    if not force and now_mono - last_check < CACHE_CHECK_INTERVAL_SEC:
        return state.bidask_last_cache_by_code
    state.bidask_last_cache_loaded_at = now_mono
    try:
        mtime = SNAPSHOT_PATH.stat().st_mtime
    except OSError:
        state.status["bidask_cache_file_exists"] = False
        return state.bidask_last_cache_by_code
    if not force and mtime == getattr(state, "bidask_last_cache_mtime", None):
        return state.bidask_last_cache_by_code
    loaded = _load_snapshot_file()
    if loaded:
        current = dict(getattr(state, "bidask_last_cache_by_code", {}) or {})
        current.update(loaded)
        state.bidask_last_cache_by_code = current
    state.bidask_last_cache_mtime = mtime
    state.status["bidask_cache_file_exists"] = True
    state.status["bidask_cache_loaded_count"] = len(getattr(state, "bidask_last_cache_by_code", {}) or {})
    state.status["bidask_cache_loaded_at"] = now_text()
    return state.bidask_last_cache_by_code


def _write_cache_if_needed(state, *, force: bool = False) -> None:
    if not getattr(state, "bidask_last_cache_dirty", False) and not force:
        return
    now_mono = time.monotonic()
    if not force and now_mono - float(getattr(state, "bidask_last_cache_saved_at_mono", 0.0) or 0.0) < CACHE_SAVE_INTERVAL_SEC:
        return
    cache = getattr(state, "bidask_last_cache_by_code", {}) or {}
    values = dict(sorted((code, entry) for code, entry in cache.items() if isinstance(entry, dict)))
    atomic_write_json(
        SNAPSHOT_PATH,
        {
            "schema_version": 1,
            "source": "stockboard_v2_bidask_last_valid",
            "trading_date": trading_date_text(),
            "ts": now_text(),
            "count": len(values),
            "values": values,
        },
    )
    state.bidask_last_cache_dirty = False
    state.bidask_last_cache_saved_at_mono = now_mono
    state.status["bidask_cache_count"] = len(values)
    state.status["bidask_cache_saved_at"] = now_text()
    state.status["bidask_cache_path"] = str(SNAPSHOT_PATH)


def _apply_entry_to_target(target: dict[str, Any], entry: dict[str, Any]) -> None:
    for key in (
        "bid_ask_ratio",
        "bid_pct",
        "ask_pct",
        "bid_volume",
        "ask_volume",
        "last_valid_bid_ask_ratio",
        "last_valid_bid_pct",
        "last_valid_ask_pct",
        "last_valid_bid_volume",
        "last_valid_ask_volume",
        "last_valid_orderbook_at",
        "orderbook_display_basis",
    ):
        if entry.get(key) not in (None, ""):
            target[key] = deepcopy(entry.get(key))
    target["bidask_last_cache_applied"] = True


def _remember_entry(state, code: str, entry: dict[str, Any]) -> None:
    code = normalize_code(code)
    if not code or not isinstance(entry, dict):
        return
    cache = _ensure_cache(state)
    cache[code] = entry
    state.bidask_last_cache_dirty = True
    state.status["bidask_last_code"] = code
    state.status["bidask_last_at"] = entry.get("updated_at") or now_text()
    state.status["bidask_cache_count"] = len(cache)

    daily_entry = state.daily_values_by_code.setdefault(code, {})
    _apply_entry_to_target(daily_entry, entry)
    quote = state.quotes.get(code)
    if isinstance(quote, dict):
        _apply_entry_to_target(quote, entry)
    if hasattr(state, "_mark_daily_dirty"):
        state._mark_daily_dirty()
    _write_cache_if_needed(state)


def _apply_cache_to_quote(state, code: str, target: dict[str, Any]) -> bool:
    code = normalize_code(code)
    if not code or not isinstance(target, dict):
        return False
    if _positive_number(target.get("bid_ask_ratio")) is not None:
        return False
    entry = _entry_from_values(code, target, source="quote_last_valid")
    if entry is None:
        cache = _ensure_cache(state)
        entry = cache.get(code)
    if entry is None:
        daily = getattr(state, "daily_values_by_code", {}).get(code)
        if isinstance(daily, dict):
            entry = _entry_from_values(code, daily, source="daily_state_last_valid")
    if entry is None:
        return False
    _apply_entry_to_target(target, entry)
    return True


def install(base) -> None:
    state_class = base.State
    if getattr(state_class, "_stockboard_bidask_last_cache_installed", False):
        return

    original_quote = state_class._quote
    original_orderbook = state_class._apply_orderbook
    original_close_metrics = state_class._apply_close_metrics
    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    def quote(self, code: str):
        result = original_quote(self, code)
        normalized = normalize_code(code)
        if isinstance(result, dict) and normalized:
            _apply_cache_to_quote(self, normalized, result)
        return result

    def apply_orderbook(self, event: dict[str, Any]) -> None:
        values = base.merged_event_values(event)
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        original_orderbook(self, event)
        if not code:
            return
        quote = self.quotes.get(code)
        if not isinstance(quote, dict):
            return
        entry = _entry_from_values(code, quote, source="orderbook_realtime")
        if entry is not None:
            _remember_entry(self, code, entry)
        else:
            _apply_cache_to_quote(self, code, quote)

    def apply_close_metrics(self, event: dict[str, Any]) -> None:
        values = base.merged_event_values(event)
        code = normalize_code(event.get("stock_code") or values.get("stock_code"))
        original_close_metrics(self, event)
        if not code:
            return
        quote = self.quotes.get(code)
        if not isinstance(quote, dict):
            return
        entry = _entry_from_values(code, values, source="orderbook_probe")
        if entry is not None:
            _remember_entry(self, code, entry)
        else:
            _apply_cache_to_quote(self, code, quote)

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        filled = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            if code and _apply_cache_to_quote(self, code, row):
                filled += 1
        self.status["bidask_cache_applied_rows"] = filled
        self.status["bidask_cache_count"] = len(_ensure_cache(self))
        return result

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        _write_cache_if_needed(self, force=force)
        return original_persist(self, force)

    state_class._quote = quote
    state_class._apply_orderbook = apply_orderbook
    state_class._apply_close_metrics = apply_close_metrics
    state_class.rows = rows
    state_class.persist_daily_state_if_needed = persist_daily_state_if_needed
    state_class._stockboard_bidask_last_cache_installed = True
