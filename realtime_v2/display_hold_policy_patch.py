from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from realtime_v2.common import (
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    now_text,
    to_number,
    trading_date_text,
)
from realtime_v2.market_session import market_session_now

DISPLAY_HOLD_PATH = RUNTIME_DIR / "display_hold_last.json"
DISPLAY_HOLD_SAVE_INTERVAL_SEC = 5.0
DISPLAY_HOLD_CHECK_INTERVAL_SEC = 5.0

DISPLAY_HOLD_KEYS = tuple(
    dict.fromkeys(
        (
            "price",
            "trade_price",
            "change_rate",
            "trade_value_eok",
            "cumulative_volume",
            "trade_time",
            "received_at",
            "source_code",
            "row_source",
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
            "one_min_strength",
            "one_min_buy_qty",
            "one_min_sell_qty",
            "one_min_strength_updated_at",
            "regular_close_strength_1m",
            "strength_5m",
            "strength_20m",
            "strength_60m",
            "strength_source",
            "strength_snapshot_at",
            "strength_status",
            "strength_display_basis",
            "last_valid_strength_1m",
            "last_valid_strength_5m",
            "last_valid_execution_strength",
            "last_valid_strength_at",
            "regular_close_bid_ask_ratio",
            "regular_close_bid_pct",
            "regular_close_ask_pct",
            "regular_close_bid_volume",
            "regular_close_ask_volume",
            "regular_close_orderbook_at",
            "orderbook_display_basis",
            "last_valid_bid_ask_ratio",
            "last_valid_bid_pct",
            "last_valid_ask_pct",
            "last_valid_bid_volume",
            "last_valid_ask_volume",
            "last_valid_orderbook_at",
            "program_net",
            "program_net_updated_at",
            "program_net_source",
            "program_net_status",
            "large_trade_buy_count",
            "large_trade_sell_count",
            "large_trade_net_count",
            "large_trade_buy_sum_eok",
            "large_trade_sell_sum_eok",
            "large_trade_net_sum_eok",
            "large_trade_source",
            "large_trade_threshold_krw",
            "large_trade_updated_at",
        )
    )
)

POSITIVE_NUMBER_KEYS = {
    "price",
    "trade_price",
    "trade_value_eok",
    "cumulative_volume",
    "execution_strength",
    "ask_volume",
    "bid_volume",
    "ask_pct",
    "bid_pct",
    "bid_ask_ratio",
    "best_ask_price",
    "best_bid_price",
    "day_open",
    "day_high",
    "day_low",
    "day_close",
    "strength_1m",
    "one_min_strength",
    "regular_close_strength_1m",
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "last_valid_strength_1m",
    "last_valid_strength_5m",
    "last_valid_execution_strength",
    "regular_close_bid_ask_ratio",
    "regular_close_bid_pct",
    "regular_close_ask_pct",
    "regular_close_bid_volume",
    "regular_close_ask_volume",
    "last_valid_bid_ask_ratio",
    "last_valid_bid_pct",
    "last_valid_ask_pct",
    "last_valid_bid_volume",
    "last_valid_ask_volume",
}

PRESENT_NUMBER_KEYS = {
    "change_rate",
    "program_net",
}

COUNTER_KEYS = {
    "large_trade_buy_count",
    "large_trade_sell_count",
    "large_trade_net_count",
    "large_trade_buy_sum_eok",
    "large_trade_sell_sum_eok",
    "large_trade_net_sum_eok",
}

STRING_KEYS = {
    "trade_time",
    "received_at",
    "source_code",
    "row_source",
    "execution_strength_updated_at",
    "orderbook_received_at",
    "one_min_strength_updated_at",
    "strength_source",
    "strength_snapshot_at",
    "strength_status",
    "strength_display_basis",
    "last_valid_strength_at",
    "regular_close_orderbook_at",
    "orderbook_display_basis",
    "last_valid_orderbook_at",
    "program_net_updated_at",
    "program_net_source",
    "program_net_status",
    "large_trade_source",
    "large_trade_updated_at",
}


def _number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    return float(number)


def _positive(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _valid_ohlc(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key in ("open", "high", "low", "close"):
        number = _positive(value.get(key))
        if number is None:
            return None
        result[key] = round(number, 4)
    for key in ("source", "date", "vwap", "prev_high", "prev_low", "prev_close"):
        if value.get(key) not in (None, ""):
            result[key] = deepcopy(value.get(key))
    return result


def _usable_value(key: str, value: Any) -> Any:
    if value in (None, ""):
        return None
    if key == "ohlc":
        return _valid_ohlc(value)
    if key in POSITIVE_NUMBER_KEYS:
        number = _positive(value)
        return None if number is None else round(number, 4)
    if key in PRESENT_NUMBER_KEYS:
        number = _number(value)
        return None if number is None else round(number, 4)
    if key in COUNTER_KEYS:
        number = _number(value)
        if number is None or number == 0:
            return None
        return int(number) if key.endswith("_count") else round(number, 4)
    if key == "large_trade_threshold_krw":
        number = _positive(value)
        return None if number is None else int(number)
    if key in STRING_KEYS:
        text = str(value).strip()
        return text if text else None
    return deepcopy(value)


def _missing_for_display(key: str, value: Any) -> bool:
    if value in (None, ""):
        return True
    if key == "ohlc":
        return _valid_ohlc(value) is None
    if key in POSITIVE_NUMBER_KEYS:
        return _positive(value) is None
    if key in PRESENT_NUMBER_KEYS:
        return _number(value) is None
    if key in COUNTER_KEYS:
        number = _number(value)
        return number is None or number == 0
    if key == "large_trade_threshold_krw":
        return _positive(value) is None
    return False


def _entry_date(entry: dict[str, Any]) -> str:
    return "".join(ch for ch in str(entry.get("trading_date") or "") if ch.isdigit())[:8]


def _entry_from_source(code: str, source: dict[str, Any], origin: str) -> dict[str, Any] | None:
    code = normalize_code(code)
    if not code or not isinstance(source, dict):
        return None
    entry: dict[str, Any] = {
        "stock_code": code,
        "trading_date": trading_date_text(),
        "display_hold_origin": origin,
        "updated_at": now_text(),
    }
    applied = 0
    for key in DISPLAY_HOLD_KEYS:
        if key not in source:
            continue
        value = _usable_value(key, source.get(key))
        if value is None:
            continue
        entry[key] = value
        applied += 1
    ohlc = _valid_ohlc(entry.get("ohlc"))
    if ohlc is not None:
        entry["ohlc"] = ohlc
        entry.setdefault("day_open", ohlc.get("open"))
        entry.setdefault("day_high", ohlc.get("high"))
        entry.setdefault("day_low", ohlc.get("low"))
        entry.setdefault("day_close", ohlc.get("close"))
        entry.setdefault("price", ohlc.get("close"))
        entry.setdefault("trade_price", ohlc.get("close"))
    if entry.get("price") not in (None, ""):
        entry.setdefault("trade_price", entry.get("price"))
    if entry.get("trade_price") not in (None, ""):
        entry.setdefault("price", entry.get("trade_price"))
    return entry if applied else None


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _load_hold_file() -> dict[str, dict[str, Any]]:
    payload = _load_json(DISPLAY_HOLD_PATH) or {}
    raw_values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(raw_values, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_code, raw_entry in raw_values.items():
        code = normalize_code(raw_code)
        if code and isinstance(raw_entry, dict):
            entry = _entry_from_source(code, raw_entry, "display_hold_file")
            if entry is not None:
                date = "".join(ch for ch in str(raw_entry.get("trading_date") or payload.get("trading_date") or "") if ch.isdigit())[:8]
                if date:
                    entry["trading_date"] = date
                result[code] = entry
    return result


def _previous_daily_state_files() -> list[Path]:
    current = str(trading_date_text())
    try:
        paths = [path for path in RUNTIME_DIR.glob("daily_state_*.json") if current not in path.name]
    except OSError:
        return []
    return sorted(paths, key=lambda path: path.stat().st_mtime if path.exists() else 0.0, reverse=True)


def _load_previous_daily_entries(state) -> dict[str, dict[str, Any]]:
    cached = getattr(state, "display_hold_previous_daily_by_code", None)
    if isinstance(cached, dict):
        return cached
    result: dict[str, dict[str, Any]] = {}
    source_path = None
    for path in _previous_daily_state_files():
        payload = _load_json(path) or {}
        raw_codes = payload.get("codes") if isinstance(payload, dict) else None
        if not isinstance(raw_codes, dict):
            continue
        payload_date = "".join(ch for ch in str(payload.get("trading_date") or "") if ch.isdigit())[:8]
        for raw_code, values in raw_codes.items():
            code = normalize_code(raw_code)
            if not code or not isinstance(values, dict):
                continue
            entry = _entry_from_source(code, values, "previous_daily_state")
            if entry is not None:
                if payload_date:
                    entry["trading_date"] = payload_date
                result[code] = entry
        if result:
            source_path = path
            break
    state.display_hold_previous_daily_by_code = result
    state.status["display_hold_previous_daily_count"] = len(result)
    if source_path is not None:
        state.status["display_hold_previous_daily_source"] = str(source_path)
    return result


def _ensure_cache(state, force: bool = False) -> dict[str, dict[str, Any]]:
    now_mono = time.monotonic()
    if not hasattr(state, "display_hold_by_code"):
        state.display_hold_by_code = {}
        state.display_hold_mtime = None
        state.display_hold_last_check = 0.0
        state.display_hold_dirty = False
        state.display_hold_last_save = 0.0
    if not force and now_mono - float(getattr(state, "display_hold_last_check", 0.0) or 0.0) < DISPLAY_HOLD_CHECK_INTERVAL_SEC:
        return state.display_hold_by_code
    state.display_hold_last_check = now_mono
    try:
        mtime = DISPLAY_HOLD_PATH.stat().st_mtime
    except OSError:
        state.status["display_hold_file_exists"] = False
        return state.display_hold_by_code
    if not force and mtime == getattr(state, "display_hold_mtime", None):
        return state.display_hold_by_code
    loaded = _load_hold_file()
    if loaded:
        current = dict(getattr(state, "display_hold_by_code", {}) or {})
        current.update(loaded)
        state.display_hold_by_code = current
    state.display_hold_mtime = mtime
    state.status["display_hold_file_exists"] = True
    state.status["display_hold_cache_count"] = len(getattr(state, "display_hold_by_code", {}) or {})
    state.status["display_hold_loaded_at"] = now_text()
    return state.display_hold_by_code


def _write_cache_if_needed(state, force: bool = False) -> None:
    if not force and not getattr(state, "display_hold_dirty", False):
        return
    now_mono = time.monotonic()
    if not force and now_mono - float(getattr(state, "display_hold_last_save", 0.0) or 0.0) < DISPLAY_HOLD_SAVE_INTERVAL_SEC:
        return
    cache = getattr(state, "display_hold_by_code", {}) or {}
    values = dict(sorted((code, entry) for code, entry in cache.items() if isinstance(entry, dict)))
    atomic_write_json(
        DISPLAY_HOLD_PATH,
        {
            "schema_version": 1,
            "source": "stockboard_v2_display_hold_last_valid",
            "trading_date": trading_date_text(),
            "ts": now_text(),
            "count": len(values),
            "values": values,
        },
    )
    state.display_hold_dirty = False
    state.display_hold_last_save = now_mono
    state.status["display_hold_cache_count"] = len(values)
    state.status["display_hold_saved_at"] = now_text()
    state.status["display_hold_path"] = str(DISPLAY_HOLD_PATH)


def _hold_session_active() -> tuple[bool, dict[str, Any]]:
    try:
        session = market_session_now().to_dict()
    except Exception:
        session = {"phase": "unknown", "accept_realtime": False, "is_trading_day": False}
    phase = str(session.get("phase") or "").lower()
    accept = bool(session.get("accept_realtime"))
    active_phases = {"after_wait", "aftermarket", "closed", "before_market", "weekend", "holiday"}
    active = phase in active_phases or not accept
    return active, session


def _remember_from_quote(state, code: str, quote: dict[str, Any], origin: str) -> None:
    code = normalize_code(code)
    if not code or not isinstance(quote, dict):
        return
    entry = _entry_from_source(code, quote, origin)
    if entry is None:
        return
    cache = _ensure_cache(state)
    cache[code] = entry
    state.display_hold_dirty = True
    state.status["display_hold_last_code"] = code
    state.status["display_hold_last_at"] = entry.get("updated_at") or now_text()
    state.status["display_hold_cache_count"] = len(cache)
    daily = state.daily_values_by_code.setdefault(code, {})
    for key, value in entry.items():
        if key in {"stock_code"}:
            continue
        if value not in (None, ""):
            daily[key] = deepcopy(value)
    if hasattr(state, "_mark_daily_dirty"):
        state._mark_daily_dirty()
    _write_cache_if_needed(state)


def _candidate_entries(state, code: str, allow_previous: bool) -> list[dict[str, Any]]:
    code = normalize_code(code)
    if not code:
        return []
    today = trading_date_text()
    result: list[dict[str, Any]] = []
    daily = getattr(state, "daily_values_by_code", {}).get(code)
    if isinstance(daily, dict):
        entry = _entry_from_source(code, daily, "current_daily_state")
        if entry is not None:
            result.append(entry)
    cache_entry = _ensure_cache(state).get(code)
    if isinstance(cache_entry, dict):
        if allow_previous or _entry_date(cache_entry) in {"", today}:
            result.append(cache_entry)
    if allow_previous:
        previous = _load_previous_daily_entries(state).get(code)
        if isinstance(previous, dict):
            result.append(previous)
    return result


def _apply_entry_to_row(row: dict[str, Any], entry: dict[str, Any]) -> int:
    applied = 0
    for key in DISPLAY_HOLD_KEYS:
        if key not in entry:
            continue
        value = _usable_value(key, entry.get(key))
        if value is None:
            continue
        if _missing_for_display(key, row.get(key)):
            row[key] = deepcopy(value)
            applied += 1
    if row.get("price") not in (None, "") and _missing_for_display("trade_price", row.get("trade_price")):
        row["trade_price"] = row.get("price")
    if row.get("trade_price") not in (None, "") and _missing_for_display("price", row.get("price")):
        row["price"] = row.get("trade_price")
    ohlc = _valid_ohlc(row.get("ohlc"))
    if ohlc is not None:
        row.setdefault("day_open", ohlc.get("open"))
        row.setdefault("day_high", ohlc.get("high"))
        row.setdefault("day_low", ohlc.get("low"))
        row.setdefault("day_close", ohlc.get("close"))
    return applied


def _apply_hold_to_row(state, row: dict[str, Any], allow_previous: bool) -> int:
    code = normalize_code(row.get("stock_code"))
    if not code:
        return 0
    total = 0
    for entry in _candidate_entries(state, code, allow_previous):
        total += _apply_entry_to_row(row, entry)
    if total:
        row["display_hold_applied"] = True
        row["display_hold_applied_count"] = total
    return total


def install(base) -> None:
    state_class = base.State
    if getattr(state_class, "_stockboard_display_hold_installed", False):
        return

    base.DAILY_PERSIST_KEYS = tuple(dict.fromkeys((*base.DAILY_PERSIST_KEYS, *DISPLAY_HOLD_KEYS)))

    original_quote = state_class._quote
    original_trade = state_class._apply_trade
    original_orderbook = state_class._apply_orderbook
    original_close_metrics = state_class._apply_close_metrics
    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    def quote(self, code: str):
        result = original_quote(self, code)
        active, _session = _hold_session_active()
        if isinstance(result, dict):
            _apply_hold_to_row(self, result, active)
        return result

    def apply_trade(self, event: dict[str, Any]) -> None:
        original_trade(self, event)
        values = base.merged_event_values(event)
        code = normalize_code(event.get("stock_code") or values.get("stock_code") or values.get("received_code"))
        quote_obj = self.quotes.get(code) if code else None
        if code and isinstance(quote_obj, dict):
            _remember_from_quote(self, code, quote_obj, "trade")

    def apply_orderbook(self, event: dict[str, Any]) -> None:
        original_orderbook(self, event)
        values = base.merged_event_values(event)
        code = normalize_code(event.get("stock_code") or values.get("stock_code") or values.get("received_code"))
        quote_obj = self.quotes.get(code) if code else None
        if code and isinstance(quote_obj, dict):
            _remember_from_quote(self, code, quote_obj, "orderbook")

    def apply_close_metrics(self, event: dict[str, Any]) -> None:
        original_close_metrics(self, event)
        values = base.merged_event_values(event)
        code = normalize_code(event.get("stock_code") or values.get("stock_code"))
        quote_obj = self.quotes.get(code) if code else None
        if code and isinstance(quote_obj, dict):
            _remember_from_quote(self, code, quote_obj, "close_metrics")

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        active, session = _hold_session_active()
        applied_rows = 0
        applied_fields = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            applied = _apply_hold_to_row(self, row, active)
            if applied:
                applied_rows += 1
                applied_fields += applied
        self.status["display_hold_active"] = active
        self.status["display_hold_phase"] = session.get("phase")
        self.status["display_hold_applied_rows"] = applied_rows
        self.status["display_hold_applied_fields"] = applied_fields
        self.status["display_hold_cache_count"] = len(_ensure_cache(self))
        return result

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        _write_cache_if_needed(self, force=force)
        return original_persist(self, force)

    state_class._quote = quote
    state_class._apply_trade = apply_trade
    state_class._apply_orderbook = apply_orderbook
    state_class._apply_close_metrics = apply_close_metrics
    state_class.rows = rows
    state_class.persist_daily_state_if_needed = persist_daily_state_if_needed
    state_class._stockboard_display_hold_installed = True
