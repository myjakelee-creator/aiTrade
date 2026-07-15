from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import (
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    now_text,
    to_number,
)
from realtime_v2.market_session import (
    last_completed_trading_date,
    market_session_now,
    next_premarket_datetime,
)


CONTINUITY_PATH = RUNTIME_DIR / "board_metric_continuity.json"
SAVE_INTERVAL_SEC = 5.0

ORDERBOOK_KEYS = (
    "bid_ask_ratio",
    "bid_pct",
    "ask_pct",
    "bid_volume",
    "ask_volume",
    "best_ask_price",
    "best_bid_price",
    "orderbook_received_at",
    "orderbook_display_basis",
    "last_valid_bid_ask_ratio",
    "last_valid_bid_pct",
    "last_valid_ask_pct",
    "last_valid_bid_volume",
    "last_valid_ask_volume",
    "last_valid_orderbook_at",
)
EXECUTION_KEYS = (
    "execution_strength",
    "execution_strength_updated_at",
    "execution_strength_source",
    "execution_strength_display_basis",
    "last_valid_execution_strength",
    "last_valid_strength_at",
)
STRENGTH5_KEYS = (
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "strength_source",
    "strength_snapshot_at",
    "strength_status",
    "strength_display_basis",
    "last_valid_strength_5m",
    "last_valid_strength_at",
)
PROGRAM_KEYS = (
    "program_net",
    "program_net_updated_at",
    "program_net_source",
    "program_net_status",
    "program_net_display_basis",
)
LARGE_TRADE_KEYS = (
    "large_trade_buy_count",
    "large_trade_sell_count",
    "large_trade_net_count",
    "large_trade_buy_sum_eok",
    "large_trade_sell_sum_eok",
    "large_trade_net_sum_eok",
    "large_trade_source",
    "large_trade_status",
    "large_trade_display_basis",
    "large_trade_threshold_krw",
    "large_trade_updated_at",
)

GROUP_KEYS = {
    "orderbook": ORDERBOOK_KEYS,
    "execution": EXECUTION_KEYS,
    "strength5": STRENGTH5_KEYS,
    "program": PROGRAM_KEYS,
    "large_trade": LARGE_TRADE_KEYS,
}
GROUP_DATE_MARKER = {
    "orderbook": "_metric_continuity_orderbook_date",
    "execution": "_metric_continuity_execution_date",
    "strength5": "_metric_continuity_strength5_date",
    "program": "_metric_continuity_program_date",
    "large_trade": "_metric_continuity_large_trade_date",
}
GROUP_TIME_KEYS = {
    "orderbook": (
        "orderbook_received_at",
        "last_valid_orderbook_at",
        "regular_close_orderbook_at",
    ),
    "execution": (
        "execution_strength_updated_at",
        "last_valid_strength_at",
    ),
    "strength5": (
        "strength_snapshot_at",
        "last_valid_strength_at",
    ),
    "program": ("program_net_updated_at",),
    "large_trade": ("large_trade_updated_at",),
}
CUMULATIVE_GROUPS = {"program", "large_trade"}
SNAPSHOT_GROUPS = {"orderbook", "execution", "strength5"}


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _positive(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _status_text(source: dict[str, Any], group: str) -> str:
    keys = {
        "execution": ("execution_strength_display_basis",),
        "strength5": ("strength_status", "strength_display_basis"),
        "program": ("program_net_status", "program_net_display_basis"),
        "large_trade": ("large_trade_status", "large_trade_display_basis"),
        "orderbook": ("orderbook_display_basis",),
    }[group]
    return " ".join(str(source.get(key) or "").strip().lower() for key in keys)


def _group_usable(source: dict[str, Any], group: str) -> bool:
    if not isinstance(source, dict):
        return False
    status = _status_text(source, group)
    if "new_session_wait" in status:
        return False
    if group == "orderbook":
        return _positive(source.get("bid_ask_ratio")) is not None
    if group == "execution":
        return _positive(source.get("execution_strength")) is not None
    if group == "strength5":
        return _positive(source.get("strength_5m")) is not None
    if group == "program":
        return _number(source.get("program_net")) is not None
    if group == "large_trade":
        values = [
            _number(source.get(key))
            for key in (
                "large_trade_buy_count",
                "large_trade_sell_count",
                "large_trade_net_count",
                "large_trade_buy_sum_eok",
                "large_trade_sell_sum_eok",
                "large_trade_net_sum_eok",
            )
            if key in source
        ]
        return bool(values) and any(value is not None for value in values)
    return False


def _group_date(source: dict[str, Any], group: str) -> str:
    marker = _date_digits(source.get(GROUP_DATE_MARKER[group]))
    if marker:
        return marker
    for key in GROUP_TIME_KEYS[group]:
        parsed = _date_digits(source.get(key))
        if parsed:
            return parsed
    for key in (
        "_session_hold_" + group + "_date",
        "_source_trading_date",
        "source_trading_date",
        "trading_date",
    ):
        parsed = _date_digits(source.get(key))
        if parsed:
            return parsed
    return ""


def _copy_group(target: dict[str, Any], source: dict[str, Any], group: str) -> int:
    copied = 0
    for key in GROUP_KEYS[group]:
        if key not in source:
            continue
        value = source.get(key)
        if value in (None, ""):
            continue
        target[key] = deepcopy(value)
        copied += 1
    source_date = _group_date(source, group)
    if source_date:
        target[GROUP_DATE_MARKER[group]] = source_date
    return copied


def _read_cache() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(CONTINUITY_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(payload, dict):
        return {}, {}
    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        return {}, payload
    values: dict[str, dict[str, Any]] = {}
    for raw_code, raw_entry in raw_values.items():
        code = normalize_code(raw_code)
        if code and isinstance(raw_entry, dict):
            values[code] = dict(raw_entry)
    return values, payload


def _ensure_cache(state) -> dict[str, dict[str, Any]]:
    cache = getattr(state, "board_metric_continuity_by_code", None)
    if isinstance(cache, dict):
        return cache
    cache, metadata = _read_cache()
    state.board_metric_continuity_by_code = cache
    state.board_metric_continuity_metadata = metadata
    state.board_metric_continuity_dirty = False
    state.board_metric_continuity_last_save_mono = 0.0
    state.status["metric_continuity_cache_count"] = len(cache)
    state.status["metric_continuity_path"] = str(CONTINUITY_PATH)
    state.status["metric_continuity_loaded_at"] = now_text()
    state.status["metric_continuity_loaded_basis_date"] = metadata.get(
        "basis_trading_date"
    )
    return cache


def _session_info(now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now()
    try:
        session = market_session_now(current).to_dict()
    except Exception as error:
        session = {
            "phase": "unknown",
            "phase_label": "장상태 알 수 없음",
            "is_trading_day": False,
            "trading_date": "",
            "calendar_date": current.strftime("%Y%m%d"),
            "accept_realtime": False,
            "reason": str(error),
            "windows": {},
        }
    phase = str(session.get("phase") or "").lower()
    started = bool(session.get("is_trading_day")) and phase not in {
        "before_market",
        "weekend",
        "holiday",
        "unknown",
    }
    current_date = _date_digits(
        session.get("trading_date") or session.get("calendar_date")
    )
    try:
        hold_until = next_premarket_datetime(current).isoformat(timespec="seconds")
    except Exception:
        hold_until = None
    try:
        reference_date = (
            current_date
            if bool(session.get("is_trading_day"))
            and phase
            in {
                "premarket",
                "opening_call",
                "regular",
                "closing_call",
                "after_wait",
                "aftermarket",
                "closed",
            }
            else last_completed_trading_date(current)
        )
    except Exception:
        reference_date = current_date
    return {
        "session": session,
        "phase": phase,
        "started": started,
        "current_date": current_date,
        "reference_date": reference_date,
        "hold_until": hold_until,
    }


def _set_basis(
    row: dict[str, Any],
    group: str,
    *,
    basis: str,
    blocked: bool,
) -> None:
    if group == "orderbook":
        row["orderbook_display_basis"] = basis
    elif group == "execution":
        row["execution_strength_display_basis"] = basis
    elif group == "strength5":
        row["strength_display_basis"] = basis
        if basis in {"previous_session_hold", "last_session_hold"}:
            row["strength_status"] = basis
    elif group == "program":
        row["program_net_display_basis"] = basis
        if basis in {"previous_session_hold", "last_session_hold"}:
            row["program_net_status"] = basis
    elif group == "large_trade":
        row["large_trade_display_basis"] = basis
        if basis in {"previous_session_hold", "last_session_hold"}:
            row["large_trade_status"] = basis
    if blocked:
        groups = row.setdefault("metric_scoring_blocked_groups", [])
        if group not in groups:
            groups.append(group)


def _reset_cumulative_group(
    row: dict[str, Any],
    group: str,
    current_date: str,
) -> int:
    if group == "program":
        row.update(
            {
                "program_net": 0.0,
                "program_net_updated_at": None,
                "program_net_source": "new_session_wait",
                "program_net_status": "new_session_wait",
                "program_net_display_basis": "new_session_wait",
            }
        )
        copied = 5
    else:
        row.update(
            {
                "large_trade_buy_count": 0,
                "large_trade_sell_count": 0,
                "large_trade_net_count": 0,
                "large_trade_buy_sum_eok": 0.0,
                "large_trade_sell_sum_eok": 0.0,
                "large_trade_net_sum_eok": 0.0,
                "large_trade_source": "new_session_wait",
                "large_trade_status": "new_session_wait",
                "large_trade_display_basis": "new_session_wait",
                "large_trade_updated_at": None,
            }
        )
        copied = 10
    if current_date:
        row[GROUP_DATE_MARKER[group]] = current_date
    groups = row.setdefault("metric_scoring_blocked_groups", [])
    if group not in groups:
        groups.append(group)
    return copied


def _restore_row(
    row: dict[str, Any],
    cached: dict[str, Any] | None,
    info: dict[str, Any],
) -> int:
    cached = cached if isinstance(cached, dict) else {}
    current_date = str(info.get("current_date") or "")
    started = bool(info.get("started"))
    copied = 0

    for group in SNAPSHOT_GROUPS:
        source = row if _group_usable(row, group) else cached
        if source is not row and _group_usable(source, group):
            copied += _copy_group(row, source, group)
        if not _group_usable(row, group):
            continue
        source_date = _group_date(row, group)
        if started and source_date and source_date != current_date:
            _set_basis(
                row,
                group,
                basis="previous_session_hold",
                blocked=True,
            )
        elif not started:
            _set_basis(row, group, basis="last_session_hold", blocked=False)
        else:
            _set_basis(row, group, basis="current_session", blocked=False)

    for group in CUMULATIVE_GROUPS:
        row_date = _group_date(row, group) if _group_usable(row, group) else ""
        if started:
            if row_date == current_date:
                _set_basis(row, group, basis="current_session", blocked=False)
                continue
            if _group_usable(cached, group) and _group_date(cached, group) == current_date:
                copied += _copy_group(row, cached, group)
                _set_basis(row, group, basis="current_session", blocked=False)
                continue
            copied += _reset_cumulative_group(row, group, current_date)
            continue

        if not _group_usable(row, group) and _group_usable(cached, group):
            copied += _copy_group(row, cached, group)
        if _group_usable(row, group):
            _set_basis(row, group, basis="last_session_hold", blocked=False)

    if copied:
        row["metric_continuity_applied"] = True
        row["metric_continuity_applied_count"] = copied
    row["metric_continuity_phase"] = info.get("phase")
    row["metric_continuity_reference_date"] = info.get("reference_date")
    row["metric_continuity_valid_until"] = info.get("hold_until")
    row["metric_continuity_basis"] = (
        "current_session" if started else "last_session_hold"
    )
    return copied


def _update_cache_from_row(
    state,
    row: dict[str, Any],
    info: dict[str, Any],
) -> bool:
    code = normalize_code(row.get("stock_code"))
    if not code:
        return False
    cache = _ensure_cache(state)
    entry = dict(cache.get(code) or {"stock_code": code})
    current_date = str(info.get("current_date") or "")
    started = bool(info.get("started"))
    changed = False

    for group in GROUP_KEYS:
        if not _group_usable(row, group):
            continue
        source_date = _group_date(row, group)
        if group in CUMULATIVE_GROUPS and started and source_date != current_date:
            continue
        before = dict(entry)
        _copy_group(entry, row, group)
        if entry != before:
            changed = True

    if changed:
        entry["stock_code"] = code
        entry["saved_at"] = now_text()
        cache[code] = entry
        state.board_metric_continuity_dirty = True
    return changed


def _write_cache_if_needed(
    state,
    info: dict[str, Any],
    *,
    force: bool = False,
) -> None:
    if not force and not bool(
        getattr(state, "board_metric_continuity_dirty", False)
    ):
        return
    now_mono = time.monotonic()
    last_save = float(
        getattr(state, "board_metric_continuity_last_save_mono", 0.0) or 0.0
    )
    if not force and now_mono - last_save < SAVE_INTERVAL_SEC:
        return
    values = dict(sorted(_ensure_cache(state).items()))
    atomic_write_json(
        CONTINUITY_PATH,
        {
            "schema_version": 1,
            "source": "stockboard_v2_board_metric_continuity",
            "ts": now_text(),
            "market_phase": info.get("phase"),
            "basis_trading_date": info.get("reference_date"),
            "valid_until": info.get("hold_until"),
            "calendar_policy": "configured_next_actual_premarket",
            "count": len(values),
            "values": values,
        },
    )
    state.board_metric_continuity_dirty = False
    state.board_metric_continuity_last_save_mono = now_mono
    state.status["metric_continuity_saved_at"] = now_text()
    state.status["metric_continuity_cache_count"] = len(values)


def install(base) -> None:
    state_class = base.State
    if getattr(state_class, "_stockboard_board_metric_continuity_installed", False):
        return

    all_keys = tuple(
        dict.fromkeys(
            key
            for keys in GROUP_KEYS.values()
            for key in keys
        )
    )
    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys(
            (
                *base.DAILY_PERSIST_KEYS,
                *all_keys,
                *GROUP_DATE_MARKER.values(),
            )
        )
    )

    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        info = _session_info()
        cache = _ensure_cache(self)
        applied_rows = 0
        applied_fields = 0
        blocked_rows = 0

        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            copied = _restore_row(row, cache.get(code), info)
            if copied:
                applied_rows += 1
                applied_fields += copied
            if row.get("metric_scoring_blocked_groups"):
                blocked_rows += 1
            _update_cache_from_row(self, row, info)

        _write_cache_if_needed(self, info)
        self.status["metric_continuity_enabled"] = True
        self.status["metric_continuity_phase"] = info.get("phase")
        self.status["metric_continuity_reference_date"] = info.get(
            "reference_date"
        )
        self.status["metric_continuity_valid_until"] = info.get("hold_until")
        self.status["metric_continuity_applied_rows"] = applied_rows
        self.status["metric_continuity_applied_fields"] = applied_fields
        self.status["metric_continuity_scoring_blocked_rows"] = blocked_rows
        self.status["metric_continuity_cache_count"] = len(cache)
        self.status["metric_continuity_policy"] = (
            "hold through closed/weekend/holiday/delayed-open before actual "
            "premarket; snapshot metrics remain visible; new-session cumulative "
            "metrics show zero until current-date values arrive"
        )
        return result

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        info = _session_info()
        _write_cache_if_needed(self, info, force=force)
        return original_persist(self, force)

    state_class.rows = rows
    state_class.persist_daily_state_if_needed = persist_daily_state_if_needed
    state_class._stockboard_board_metric_continuity_installed = True
