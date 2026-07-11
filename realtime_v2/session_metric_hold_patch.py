from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, normalize_code, now_text, to_number
from realtime_v2.market_session import market_session_now

SESSION_HOLD_PATH = RUNTIME_DIR / "session_metric_hold_last.json"
SAVE_INTERVAL_SEC = 5.0
MAX_DAILY_FILES = 20

ORDERBOOK_KEYS = (
    "bid_ask_ratio","bid_pct","ask_pct","bid_volume","ask_volume",
    "best_ask_price","best_bid_price","orderbook_received_at","orderbook_display_basis",
    "regular_close_bid_ask_ratio","regular_close_bid_pct","regular_close_ask_pct",
    "regular_close_bid_volume","regular_close_ask_volume","regular_close_orderbook_at",
    "last_valid_bid_ask_ratio","last_valid_bid_pct","last_valid_ask_pct",
    "last_valid_bid_volume","last_valid_ask_volume","last_valid_orderbook_at",
)
EXECUTION_KEYS = (
    "execution_strength","execution_strength_updated_at",
    "last_valid_execution_strength","last_valid_strength_at",
)
STRENGTH5_KEYS = (
    "strength_5m","strength_20m","strength_60m","strength_source",
    "strength_snapshot_at","strength_status","strength_display_basis",
    "last_valid_strength_5m","last_valid_strength_at",
)
PROGRAM_KEYS = (
    "program_net","program_net_updated_at","program_net_source","program_net_status",
)
LARGE_TRADE_KEYS = (
    "large_trade_buy_count","large_trade_sell_count","large_trade_net_count",
    "large_trade_buy_sum_eok","large_trade_sell_sum_eok","large_trade_net_sum_eok",
    "large_trade_source","large_trade_threshold_krw","large_trade_updated_at",
)

GROUP_KEYS = {
    "orderbook": ORDERBOOK_KEYS,
    "execution": EXECUTION_KEYS,
    "strength5": STRENGTH5_KEYS,
    "program": PROGRAM_KEYS,
    "large_trade": LARGE_TRADE_KEYS,
}
GROUP_DATE_MARKER = {
    "orderbook": "_session_hold_orderbook_date",
    "execution": "_session_hold_execution_date",
    "strength5": "_session_hold_strength5_date",
    "program": "_session_hold_program_date",
    "large_trade": "_session_hold_large_trade_date",
}
GROUP_TIME_KEYS = {
    "orderbook": ("orderbook_received_at","last_valid_orderbook_at","regular_close_orderbook_at"),
    "execution": ("execution_strength_updated_at","last_valid_strength_at"),
    "strength5": ("strength_snapshot_at","last_valid_strength_at"),
    "program": ("program_net_updated_at",),
    "large_trade": ("large_trade_updated_at",),
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _positive(value: Any) -> float | None:
    number = to_number(value)
    if number is None or float(number) <= 0:
        return None
    return float(number)


def _numeric(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _group_date(source: dict[str, Any], group: str) -> str:
    marker_date = _date_digits(source.get(GROUP_DATE_MARKER[group]))
    if marker_date:
        return marker_date
    for key in GROUP_TIME_KEYS[group]:
        parsed = _date_digits(source.get(key))
        if parsed:
            return parsed
    for key in ("_source_trading_date","source_trading_date","trading_date"):
        parsed = _date_digits(source.get(key))
        if parsed:
            return parsed
    return ""


def _group_usable(source: dict[str, Any], group: str) -> bool:
    if not isinstance(source, dict):
        return False
    if group == "orderbook":
        return _positive(source.get("bid_ask_ratio")) is not None
    if group == "execution":
        return _positive(source.get("execution_strength")) is not None
    if group == "strength5":
        return _positive(source.get("strength_5m")) is not None
    if group == "program":
        if "program_net" not in source:
            return False
        number = _numeric(source.get("program_net"))
        if number is None:
            return False
        if number != 0:
            return True
        return bool(
            source.get("program_net_updated_at")
            or source.get("program_net_source")
            or source.get("program_net_status")
            or source.get(GROUP_DATE_MARKER[group])
        )
    if group == "large_trade":
        values = [
            _numeric(source.get(key))
            for key in (
                "large_trade_buy_count","large_trade_sell_count","large_trade_net_count",
                "large_trade_buy_sum_eok","large_trade_sell_sum_eok","large_trade_net_sum_eok",
            )
            if key in source
        ]
        if not values or all(value is None for value in values):
            return False
        if any(value not in (None, 0.0) for value in values):
            return True
        return bool(
            source.get("large_trade_updated_at")
            or source.get("large_trade_source")
            or source.get(GROUP_DATE_MARKER[group])
        )
    return False


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


def _clear_cumulative_group(row: dict[str, Any], group: str) -> None:
    if group == "program":
        row["program_net"] = None
        row["program_net_updated_at"] = None
        row["program_net_source"] = "new_session_wait"
        row["program_net_status"] = "new_session_wait"
    elif group == "large_trade":
        row["large_trade_buy_count"] = 0
        row["large_trade_sell_count"] = 0
        row["large_trade_net_count"] = 0
        row["large_trade_buy_sum_eok"] = 0.0
        row["large_trade_sell_sum_eok"] = 0.0
        row["large_trade_net_sum_eok"] = 0.0
        row["large_trade_source"] = "new_session_reset"
        row["large_trade_updated_at"] = None
    row.pop(GROUP_DATE_MARKER[group], None)


def _session_dict() -> dict[str, Any]:
    try:
        return market_session_now().to_dict()
    except Exception:
        return {
            "phase": "unknown",
            "is_trading_day": False,
            "trading_date": "",
            "calendar_date": "",
            "accept_realtime": False,
        }


def _session_started(session: dict[str, Any]) -> bool:
    if not bool(session.get("is_trading_day")):
        return False
    phase = str(session.get("phase") or "").lower()
    return phase not in {"before_market","weekend","holiday","unknown"}


def _ensure_hold_cache(state) -> dict[str, dict[str, Any]]:
    cache = getattr(state, "session_metric_hold_by_code", None)
    if isinstance(cache, dict):
        return cache
    payload = _read_json(SESSION_HOLD_PATH) or {}
    raw_values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
    cache = {}
    for raw_code, raw_entry in raw_values.items():
        code = normalize_code(raw_code)
        if code and isinstance(raw_entry, dict):
            cache[code] = dict(raw_entry)
    state.session_metric_hold_by_code = cache
    state.session_metric_hold_dirty = False
    state.session_metric_hold_last_save = 0.0
    return cache


def _merge_source_groups(target: dict[str, Any], source: dict[str, Any]) -> None:
    for group in GROUP_KEYS:
        if _group_usable(target, group):
            continue
        if _group_usable(source, group):
            _copy_group(target, source, group)


def _load_daily_history(state) -> dict[str, dict[str, Any]]:
    cached = getattr(state, "session_metric_daily_history_by_code", None)
    if isinstance(cached, dict):
        return cached
    try:
        paths = sorted(
            RUNTIME_DIR.glob("daily_state_*.json"),
            key=lambda item: item.stat().st_mtime if item.exists() else 0.0,
            reverse=True,
        )[:MAX_DAILY_FILES]
    except OSError:
        paths = []
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        payload = _read_json(path) or {}
        codes = payload.get("codes") if isinstance(payload.get("codes"), dict) else None
        if not isinstance(codes, dict):
            continue
        payload_date = _date_digits(payload.get("trading_date")) or _date_digits(path.stem)
        for raw_code, raw_values in codes.items():
            code = normalize_code(raw_code)
            if not code or not isinstance(raw_values, dict):
                continue
            source = dict(raw_values)
            if payload_date:
                source["_source_trading_date"] = payload_date
            target = result.setdefault(code, {})
            _merge_source_groups(target, source)
    state.session_metric_daily_history_by_code = result
    state.status["session_metric_daily_history_count"] = len(result)
    return result


def _strength_snapshot_paths(base) -> list[Path]:
    root = Path(getattr(base, "ROOT", Path(__file__).resolve().parents[1]))
    return [
        root / "data" / "runtime" / "stockboard_close_metrics_snapshots.json",
        RUNTIME_DIR / "strength_snapshot.json",
    ]


def _load_strength_history(state, base) -> dict[str, dict[str, Any]]:
    cached = getattr(state, "session_metric_strength_history_by_code", None)
    if isinstance(cached, dict):
        return cached
    candidates: list[tuple[float, Path]] = []
    for path in _strength_snapshot_paths(base):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    result: dict[str, dict[str, Any]] = {}
    for _mtime, path in sorted(candidates, reverse=True):
        payload = _read_json(path) or {}
        raw_values = payload.get("snapshots") if isinstance(payload.get("snapshots"), dict) else payload.get("values")
        if not isinstance(raw_values, dict):
            continue
        payload_date = _date_digits(payload.get("trading_date"))
        for raw_code, raw_entry in raw_values.items():
            if not isinstance(raw_entry, dict):
                continue
            code = normalize_code(raw_entry.get("stock_code") or raw_code)
            if not code:
                continue
            strength = _positive(raw_entry.get("strength_5m"))
            if strength is None:
                continue
            source = {
                "strength_5m": round(strength, 4),
                "strength_20m": raw_entry.get("strength_20m"),
                "strength_60m": raw_entry.get("strength_60m"),
                "strength_source": raw_entry.get("strength_source") or payload.get("source") or "opt10046_cached",
                "strength_snapshot_at": raw_entry.get("strength_snapshot_at")
                or raw_entry.get("updated_at")
                or payload.get("ts"),
                "strength_status": raw_entry.get("strength_status") or "cached_previous_session",
            }
            source_date = _date_digits(raw_entry.get("trading_date")) or payload_date
            if source_date:
                source["_source_trading_date"] = source_date
            target = result.setdefault(code, {})
            if not _group_usable(target, "strength5"):
                _copy_group(target, source, "strength5")
    state.session_metric_strength_history_by_code = result
    state.status["session_metric_strength_history_count"] = len(result)
    return result


def _sources_for_code(state, base, code: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = [row]
    daily = getattr(state, "daily_values_by_code", {}).get(code)
    if isinstance(daily, dict):
        sources.append(daily)
    hold = _ensure_hold_cache(state).get(code)
    if isinstance(hold, dict):
        sources.append(hold)
    history = _load_daily_history(state).get(code)
    if isinstance(history, dict):
        sources.append(history)
    strength = _load_strength_history(state, base).get(code)
    if isinstance(strength, dict):
        sources.append(strength)
    return sources


def _first_source(sources: list[dict[str, Any]], group: str, required_date: str | None = None) -> dict[str, Any] | None:
    for source in sources:
        if not _group_usable(source, group):
            continue
        if required_date and _group_date(source, group) != required_date:
            continue
        return source
    return None


def _restore_row(state, base, row: dict[str, Any], session: dict[str, Any]) -> tuple[int, bool]:
    code = normalize_code(row.get("stock_code"))
    if not code:
        return 0, False
    sources = _sources_for_code(state, base, code, row)
    current_date = _date_digits(session.get("trading_date") or session.get("calendar_date"))
    started = _session_started(session)
    copied = 0

    for group in ("orderbook","execution","strength5"):
        if _group_usable(row, group):
            continue
        source = _first_source(sources[1:], group)
        if source is not None:
            copied += _copy_group(row, source, group)

    for group in ("program","large_trade"):
        if started:
            row_date = _group_date(row, group) if _group_usable(row, group) else ""
            if not row_date or row_date != current_date:
                source = _first_source(sources[1:], group, required_date=current_date)
                if source is not None:
                    copied += _copy_group(row, source, group)
                else:
                    _clear_cumulative_group(row, group)
        elif not _group_usable(row, group):
            source = _first_source(sources[1:], group)
            if source is not None:
                copied += _copy_group(row, source, group)

    if copied:
        row["session_metric_hold_applied"] = True
        row["session_metric_hold_applied_count"] = copied
    return copied, started


def _update_hold_entry(state, code: str, row: dict[str, Any], session: dict[str, Any]) -> bool:
    cache = _ensure_hold_cache(state)
    entry = dict(cache.get(code) or {"stock_code": code})
    current_date = _date_digits(session.get("trading_date") or session.get("calendar_date"))
    started = _session_started(session)
    changed = False

    for group in ("orderbook","execution","strength5"):
        if not _group_usable(row, group):
            continue
        before = dict(entry)
        _copy_group(entry, row, group)
        if entry != before:
            changed = True

    for group in ("program","large_trade"):
        if not _group_usable(row, group):
            continue
        source_date = _group_date(row, group)
        if started and (not source_date or source_date != current_date):
            continue
        before = dict(entry)
        _copy_group(entry, row, group)
        if entry != before:
            changed = True

    if changed:
        entry["stock_code"] = code
        entry["saved_at"] = now_text()
        cache[code] = entry
        state.session_metric_hold_dirty = True
    return changed


def _write_hold_if_needed(state, force: bool = False) -> None:
    if not force and not bool(getattr(state, "session_metric_hold_dirty", False)):
        return
    now_mono = time.monotonic()
    last_save = float(getattr(state, "session_metric_hold_last_save", 0.0) or 0.0)
    if not force and now_mono - last_save < SAVE_INTERVAL_SEC:
        return
    values = dict(sorted(_ensure_hold_cache(state).items()))
    atomic_write_json(
        SESSION_HOLD_PATH,
        {
            "schema_version": 1,
            "source": "stockboard_v2_session_metric_hold",
            "ts": now_text(),
            "count": len(values),
            "values": values,
        },
    )
    state.session_metric_hold_dirty = False
    state.session_metric_hold_last_save = now_mono
    state.status["session_metric_hold_saved_at"] = now_text()
    state.status["session_metric_hold_path"] = str(SESSION_HOLD_PATH)
    state.status["session_metric_hold_cache_count"] = len(values)


def install(base) -> None:
    state_class = base.State
    if getattr(state_class, "_stockboard_session_metric_hold_installed", False):
        return

    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        session = _session_dict()
        applied_rows = 0
        applied_fields = 0
        started = _session_started(session)
        for row in result:
            if not isinstance(row, dict):
                continue
            copied, _ = _restore_row(self, base, row, session)
            if copied:
                applied_rows += 1
                applied_fields += copied
            code = normalize_code(row.get("stock_code"))
            if code:
                _update_hold_entry(self, code, row, session)
        _write_hold_if_needed(self)
        self.status["session_metric_hold_phase"] = session.get("phase")
        self.status["session_metric_hold_session_started"] = started
        self.status["session_metric_hold_applied_rows"] = applied_rows
        self.status["session_metric_hold_applied_fields"] = applied_fields
        self.status["session_metric_hold_cache_count"] = len(_ensure_hold_cache(self))
        return result

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        _write_hold_if_needed(self, force=force)
        return original_persist(self, force)

    state_class.rows = rows
    state_class.persist_daily_state_if_needed = persist_daily_state_if_needed
    state_class._stockboard_session_metric_hold_installed = True
