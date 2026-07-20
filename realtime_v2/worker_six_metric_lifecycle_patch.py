from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, normalize_code, now_text, to_number
from realtime_v2.market_session import (
    last_completed_trading_date,
    market_session_now,
    next_premarket_datetime,
)

PATCH_VERSION = "six_metric_lifecycle_v1"
SNAPSHOT_PATH = RUNTIME_DIR / "six_metric_lifecycle.json"
SESSION_HOLD_PATH = RUNTIME_DIR / "session_metric_hold_last.json"
SAVE_INTERVAL_SEC = 5.0
CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}
LIVE_EXECUTION_SOURCE = "kiwoom_rest_ws_0B_fid228"
HELD_EXECUTION_SOURCE = "kiwoom_rest_ws_0B_fid228_close_hold"

GROUPS: dict[str, dict[str, Any]] = {
    "amount_ratio": {
        "display_keys": ("amount_ratio",),
        "capture_keys": (
            "amount_ratio",
            "trade_value_eok",
            "prev_trade_value_eok",
            "amount_ratio_source",
            "amount_ratio_status",
            "amount_ratio_updated_at",
        ),
        "date_keys": (
            "amount_ratio_updated_at",
            "received_at",
            "price_received_at",
            "trade_received_at",
            "_session_hold_amount_ratio_date",
        ),
        "source_key": "amount_ratio_source",
        "status_key": "amount_ratio_status",
        "positive_only": True,
        "zero_valid": False,
    },
    "orderbook": {
        "display_keys": (
            "bid_ask_ratio",
            "bid_pct",
            "ask_pct",
            "bid_volume",
            "ask_volume",
            "best_ask_price",
            "best_bid_price",
            "last_valid_bid_ask_ratio",
            "last_valid_bid_pct",
            "last_valid_ask_pct",
            "last_valid_bid_volume",
            "last_valid_ask_volume",
        ),
        "capture_keys": (
            "bid_ask_ratio",
            "bid_pct",
            "ask_pct",
            "bid_volume",
            "ask_volume",
            "best_ask_price",
            "best_bid_price",
            "last_valid_bid_ask_ratio",
            "last_valid_bid_pct",
            "last_valid_ask_pct",
            "last_valid_bid_volume",
            "last_valid_ask_volume",
            "orderbook_source",
            "orderbook_status",
            "orderbook_received_at",
            "orderbook_display_basis",
            "last_valid_orderbook_at",
        ),
        "date_keys": (
            "_session_hold_orderbook_date",
            "orderbook_received_at",
            "last_valid_orderbook_at",
            "regular_close_orderbook_at",
        ),
        "source_key": "orderbook_source",
        "status_key": "orderbook_status",
        "positive_only": True,
        "zero_valid": False,
    },
    "execution": {
        "display_keys": (
            "execution_strength",
            "last_valid_execution_strength",
        ),
        "capture_keys": (
            "execution_strength",
            "last_valid_execution_strength",
            "execution_strength_source",
            "execution_strength_status",
            "execution_strength_updated_at",
            "execution_strength_received_at",
            "execution_strength_source_time",
            "execution_strength_exchange",
            "execution_strength_market_phase",
            "execution_strength_trade_price",
            "last_valid_strength_at",
        ),
        "date_keys": (
            "execution_strength_received_at",
            "execution_strength_updated_at",
            "last_valid_strength_at",
            "_session_hold_execution_date",
        ),
        "source_key": "execution_strength_source",
        "status_key": "execution_strength_status",
        "positive_only": True,
        "zero_valid": False,
    },
    "strength5": {
        "display_keys": (
            "strength_5m",
            "strength_20m",
            "strength_60m",
            "last_valid_strength_5m",
        ),
        "capture_keys": (
            "strength_5m",
            "strength_20m",
            "strength_60m",
            "last_valid_strength_5m",
            "strength_source",
            "strength_status",
            "strength_snapshot_at",
            "strength_display_basis",
            "last_valid_strength_at",
        ),
        "date_keys": (
            "_session_hold_strength5_date",
            "strength_snapshot_at",
            "last_valid_strength_at",
        ),
        "source_key": "strength_source",
        "status_key": "strength_status",
        "positive_only": True,
        "zero_valid": False,
    },
    "program": {
        "display_keys": ("program_net",),
        "capture_keys": (
            "program_net",
            "program_net_source",
            "program_net_status",
            "program_net_updated_at",
        ),
        "date_keys": (
            "_session_hold_program_date",
            "program_net_updated_at",
        ),
        "source_key": "program_net_source",
        "status_key": "program_net_status",
        "positive_only": False,
        "zero_valid": True,
    },
    "large_trade": {
        "display_keys": (
            "large_trade_buy_count",
            "large_trade_sell_count",
            "large_trade_net_count",
            "large_trade_buy_sum_eok",
            "large_trade_sell_sum_eok",
            "large_trade_net_sum_eok",
        ),
        "capture_keys": (
            "large_trade_buy_count",
            "large_trade_sell_count",
            "large_trade_net_count",
            "large_trade_buy_sum_eok",
            "large_trade_sell_sum_eok",
            "large_trade_net_sum_eok",
            "large_trade_source",
            "large_trade_status",
            "large_trade_threshold_krw",
            "large_trade_updated_at",
            "large_trade_source_trading_date",
        ),
        "date_keys": (
            "large_trade_source_trading_date",
            "_session_hold_large_trade_date",
            "large_trade_trading_date",
            "large_trade_updated_at",
        ),
        "source_key": "large_trade_source",
        "status_key": "large_trade_status",
        "positive_only": False,
        "zero_valid": True,
    },
}

PERSIST_KEYS = tuple(
    dict.fromkeys(
        key
        for config in GROUPS.values()
        for key in config["capture_keys"]
    )
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_clock(value: Any, fallback: str = "08:00") -> clock_time:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text[:2])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return clock_time(hour, minute)
    except (TypeError, ValueError):
        pass
    hour_text, minute_text = fallback.split(":", 1)
    return clock_time(int(hour_text), int(minute_text))


def _next_premarket_boundary(now: datetime | None = None) -> datetime:
    current = now or datetime.now()
    session = market_session_now(current)
    windows = session.windows if isinstance(session.windows, dict) else {}
    today_premarket = datetime.combine(
        current.date(),
        _parse_clock(windows.get("premarket_start"), "08:00"),
    )
    if session.is_trading_day and current < today_premarket:
        return today_premarket
    return next_premarket_datetime(current)


def _expected_date(session, now: datetime) -> str:
    phase = str(session.phase or "")
    if phase in CLOSED_PHASES:
        return last_completed_trading_date(now)
    return _date_digits(session.trading_date or session.calendar_date)


def _group_date(values: dict[str, Any], group: str, fallback: str = "") -> str:
    for key in GROUPS[group]["date_keys"]:
        date_text = _date_digits(values.get(key))
        if date_text:
            return date_text
    return _date_digits(fallback)


def _execution_source_allowed(values: dict[str, Any]) -> bool:
    source = str(values.get("execution_strength_source") or "")
    return source in {LIVE_EXECUTION_SOURCE, HELD_EXECUTION_SOURCE}


def _prepare_amount_ratio(values: dict[str, Any]) -> dict[str, Any]:
    result = dict(values)
    ratio = _number(result.get("amount_ratio"))
    current = _number(result.get("trade_value_eok"))
    previous = _number(result.get("prev_trade_value_eok"))
    if (ratio is None or ratio <= 0) and current is not None and current >= 0 and previous is not None and previous > 0:
        ratio = current / previous
        result["amount_ratio"] = round(ratio, 4)
    if ratio is not None and ratio > 0:
        result.setdefault("amount_ratio_source", "trade_value_current_div_previous")
        result.setdefault("amount_ratio_status", "ok")
        result.setdefault(
            "amount_ratio_updated_at",
            result.get("received_at")
            or result.get("price_received_at")
            or result.get("trade_received_at"),
        )
    return result


def _group_usable(values: dict[str, Any], group: str) -> bool:
    if not isinstance(values, dict):
        return False
    if group == "execution" and not _execution_source_allowed(values):
        return False
    prepared = _prepare_amount_ratio(values) if group == "amount_ratio" else values
    numbers = [
        _number(prepared.get(key))
        for key in GROUPS[group]["display_keys"]
        if key in prepared
    ]
    numbers = [number for number in numbers if number is not None]
    if not numbers:
        return False
    if bool(GROUPS[group].get("positive_only")):
        return any(number > 0 for number in numbers)
    if any(number != 0.0 for number in numbers):
        return True
    if not bool(GROUPS[group].get("zero_valid")):
        return False
    return bool(
        prepared.get(GROUPS[group]["source_key"])
        or prepared.get(GROUPS[group]["status_key"])
        or _group_date(prepared, group)
    )


def _capture_values(values: dict[str, Any], group: str) -> dict[str, Any]:
    prepared = _prepare_amount_ratio(values) if group == "amount_ratio" else values
    result: dict[str, Any] = {}
    for key in GROUPS[group]["capture_keys"]:
        value = prepared.get(key)
        if value not in (None, ""):
            result[key] = deepcopy(value)
    return result


def _entry_from_values(
    code: str,
    group: str,
    values: dict[str, Any],
    *,
    source_date: str,
    now: datetime,
) -> dict[str, Any] | None:
    code = normalize_code(code)
    prepared = _prepare_amount_ratio(values) if group == "amount_ratio" else dict(values)
    if not code or not _group_usable(prepared, group):
        return None
    date_text = _group_date(prepared, group, source_date)
    if not date_text:
        return None
    captured_values = _capture_values(prepared, group)
    if not captured_values:
        return None
    return {
        "stock_code": code,
        "group": group,
        "source_trading_date": date_text,
        "captured_at": now_text(),
        "expires_at": _next_premarket_boundary(now).isoformat(timespec="seconds"),
        "values": captured_values,
    }


def _entry_valid(
    entry: dict[str, Any],
    *,
    expected_date: str,
    group: str,
    now: datetime,
) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(entry.get("group") or "") != group:
        return False
    if _date_digits(entry.get("source_trading_date")) != _date_digits(expected_date):
        return False
    try:
        expires_at = datetime.fromisoformat(str(entry.get("expires_at") or ""))
    except ValueError:
        return False
    values = entry.get("values")
    return now < expires_at and isinstance(values, dict) and _group_usable(values, group)


def _empty_cache() -> dict[str, dict[str, dict[str, Any]]]:
    return {group: {} for group in GROUPS}


def _merge_payload(
    cache: dict[str, dict[str, dict[str, Any]]],
    payload: dict[str, Any],
    *,
    payload_date: str,
    now: datetime,
) -> None:
    raw_groups = payload.get("groups") if isinstance(payload.get("groups"), dict) else None
    if raw_groups is not None:
        for group, raw_codes in raw_groups.items():
            if group not in GROUPS or not isinstance(raw_codes, dict):
                continue
            for raw_code, entry in raw_codes.items():
                code = normalize_code(raw_code)
                if not code or not isinstance(entry, dict):
                    continue
                cache[group][code] = dict(entry)
        return

    raw_codes = payload.get("values") if isinstance(payload.get("values"), dict) else None
    if raw_codes is None:
        raw_codes = payload.get("codes") if isinstance(payload.get("codes"), dict) else {}
    for raw_code, raw_values in raw_codes.items():
        code = normalize_code(raw_code)
        if not code or not isinstance(raw_values, dict):
            continue
        for group in GROUPS:
            entry = _entry_from_values(
                code,
                group,
                raw_values,
                source_date=payload_date,
                now=now,
            )
            if entry is not None:
                cache[group][code] = entry


def _load_cache(now: datetime | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    current = now or datetime.now()
    session = market_session_now(current)
    expected = _expected_date(session, current)
    cache = _empty_cache()

    snapshot = _read_json(SNAPSHOT_PATH)
    _merge_payload(
        cache,
        snapshot,
        payload_date=_date_digits(snapshot.get("source_trading_date")),
        now=current,
    )

    session_hold = _read_json(SESSION_HOLD_PATH)
    _merge_payload(
        cache,
        session_hold,
        payload_date=_date_digits(session_hold.get("trading_date")),
        now=current,
    )

    if expected:
        daily_path = RUNTIME_DIR / f"daily_state_{expected}.json"
        daily = _read_json(daily_path)
        _merge_payload(
            cache,
            daily,
            payload_date=_date_digits(daily.get("trading_date")) or expected,
            now=current,
        )

    for group in GROUPS:
        cache[group] = {
            code: entry
            for code, entry in cache[group].items()
            if _entry_valid(entry, expected_date=expected, group=group, now=current)
        }
    return cache


def _clear_display(row: dict[str, Any], group: str, status: str) -> None:
    config = GROUPS[group]
    for key in config["display_keys"]:
        row.pop(key, None)
    row[config["status_key"]] = status
    row[f"{group}_available"] = False


def _overlay_entry(
    row: dict[str, Any],
    group: str,
    entry: dict[str, Any],
    *,
    phase: str,
) -> None:
    values = deepcopy(entry.get("values") or {})
    for key in GROUPS[group]["capture_keys"]:
        value = values.get(key)
        if value not in (None, ""):
            row[key] = value
    row[f"{group}_available"] = True
    row[f"{group}_source_trading_date"] = entry.get("source_trading_date")
    row[f"{group}_hold_until"] = entry.get("expires_at")
    row[f"{group}_display_basis"] = (
        "previous_session_final_until_premarket"
        if phase in CLOSED_PHASES
        else "same_session_last_valid"
    )
    status_key = GROUPS[group]["status_key"]
    if phase in CLOSED_PHASES:
        row[status_key] = "previous_session_final_hold"
    elif str(row.get(status_key) or "") in {"", "missing", "unavailable"}:
        row[status_key] = "same_session_last_valid"


def install(base) -> None:
    """Apply one authoritative lifecycle to six StockBoard metrics.

    No collector, QAx registration, realtime FID, REST call, WebSocket connection,
    sorting rule, or browser calculation is added. One small runtime snapshot is
    updated at most every five seconds when accepted metric values changed.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(state_class, "_stockboard_six_metric_lifecycle_installed", False):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *PERSIST_KEYS))
    )
    original_state_init = state_class.__init__
    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        cache = _load_cache()
        self.six_metric_lifecycle_by_group = cache
        self.six_metric_lifecycle_dirty = False
        self.six_metric_lifecycle_last_save_mono = 0.0
        with self.lock:
            self.status.update(
                {
                    "six_metric_lifecycle_installed": True,
                    "six_metric_lifecycle_version": PATCH_VERSION,
                    "six_metric_lifecycle_path": str(SNAPSHOT_PATH),
                    "six_metric_lifecycle_loaded_count": sum(
                        len(values) for values in cache.values()
                    ),
                    "six_metric_lifecycle_loaded_at": now_text(),
                }
            )

    def write_snapshot(self, force: bool = False) -> bool:
        now_mono = time.monotonic()
        with self.lock:
            dirty = bool(getattr(self, "six_metric_lifecycle_dirty", False))
            last_save = float(getattr(self, "six_metric_lifecycle_last_save_mono", 0.0) or 0.0)
            if not force and (not dirty or now_mono - last_save < SAVE_INTERVAL_SEC):
                return False
            groups = deepcopy(getattr(self, "six_metric_lifecycle_by_group", _empty_cache()))
        payload = {
            "schema_version": 1,
            "source": "stockboard_six_metric_lifecycle",
            "updated_at": now_text(),
            "groups": groups,
        }
        atomic_write_json(SNAPSHOT_PATH, payload)
        with self.lock:
            self.six_metric_lifecycle_dirty = False
            self.six_metric_lifecycle_last_save_mono = now_mono
            self.status["six_metric_lifecycle_last_saved_at"] = payload["updated_at"]
            self.status["six_metric_lifecycle_saved_count"] = sum(
                len(values) for values in groups.values()
            )
            self.status["six_metric_lifecycle_last_error"] = None
        return True

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        now = datetime.now()
        session = market_session_now(now)
        phase = str(session.phase or "")
        expected = _expected_date(session, now)
        current_date = _date_digits(session.trading_date or session.calendar_date)
        cache = getattr(self, "six_metric_lifecycle_by_group", None)
        if not isinstance(cache, dict):
            cache = _empty_cache()
            self.six_metric_lifecycle_by_group = cache

        captured = {group: 0 for group in GROUPS}
        held = {group: 0 for group in GROUPS}
        hidden = {group: 0 for group in GROUPS}
        live = {group: 0 for group in GROUPS}

        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            if not code:
                continue
            prepared_row = _prepare_amount_ratio(row)
            if prepared_row is not row:
                row.update(prepared_row)

            for group in GROUPS:
                source_date = _group_date(row, group)
                current_valid = (
                    _group_usable(row, group)
                    and bool(source_date)
                    and source_date == expected
                )
                if current_valid:
                    live[group] += 1
                    row[f"{group}_available"] = True
                    row[f"{group}_source_trading_date"] = source_date
                    entry = _entry_from_values(
                        code,
                        group,
                        row,
                        source_date=source_date,
                        now=now,
                    )
                    if entry is not None and cache.setdefault(group, {}).get(code) != entry:
                        cache[group][code] = entry
                        self.six_metric_lifecycle_dirty = True
                        captured[group] += 1
                    continue

                entry = cache.setdefault(group, {}).get(code)
                if _entry_valid(entry, expected_date=expected, group=group, now=now):
                    _overlay_entry(row, group, entry, phase=phase)
                    held[group] += 1
                    continue

                if _group_usable(row, group):
                    _clear_display(row, group, "source_date_mismatch_hidden")
                    row[f"{group}_source_trading_date"] = source_date or None
                    row[f"{group}_expected_trading_date"] = expected or None
                    hidden[group] += 1
                elif group == "amount_ratio" and current_date and phase in {
                    "after_wait",
                    "aftermarket",
                }:
                    row["amount_ratio_status"] = "same_session_final_wait"

        with self.lock:
            self.status["six_metric_lifecycle_phase"] = phase
            self.status["six_metric_lifecycle_expected_date"] = expected
            for group in GROUPS:
                self.status[f"six_metric_{group}_live_count"] = live[group]
                self.status[f"six_metric_{group}_held_count"] = held[group]
                self.status[f"six_metric_{group}_hidden_count"] = hidden[group]
                self.status[f"six_metric_{group}_captured_count"] = captured[group]
                self.status[f"six_metric_{group}_cache_count"] = len(
                    cache.setdefault(group, {})
                )
        if getattr(self, "six_metric_lifecycle_dirty", False):
            try:
                write_snapshot(self, force=False)
            except Exception as error:
                with self.lock:
                    self.status["six_metric_lifecycle_last_error"] = (
                        f"{type(error).__name__}: {error}"
                    )
        return result

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        try:
            write_snapshot(self, force=force)
        except Exception as error:
            with self.lock:
                self.status["six_metric_lifecycle_last_error"] = (
                    f"{type(error).__name__}: {error}"
                )
        return original_persist(self, force)

    state_class.__init__ = state_init
    state_class.rows = rows
    state_class.persist_daily_state_if_needed = persist_daily_state_if_needed
    state_class.flush_six_metric_lifecycle = write_snapshot
    state_class._stockboard_six_metric_lifecycle_installed = True
