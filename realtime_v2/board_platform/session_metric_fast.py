from __future__ import annotations

from copy import deepcopy
from typing import Any


def _date_digits(value: Any) -> str:
    if value in (None, ""):
        return ""
    text = str(value)
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    if len(text) >= 10 and text[4:5] in {"-", "/", "."} and text[7:8] == text[4:5]:
        compact = text[:4] + text[5:7] + text[8:10]
        if compact.isdigit():
            return compact
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _numeric(module: Any, value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    number = module.to_number(value)
    return None if number is None else float(number)


def _positive(module: Any, value: Any) -> float | None:
    number = _numeric(module, value)
    if number is None or number <= 0:
        return None
    return number


def _row_token(module: Any, row: dict[str, Any], session: dict[str, Any]) -> tuple[Any, ...]:
    current_date = _date_digits(session.get("trading_date") or session.get("calendar_date"))
    started = module._session_started(session)
    values: list[Any] = [current_date, started]
    for group in module.GROUP_KEYS:
        values.append(group)
        for key in module.GROUP_KEYS[group]:
            values.append(row.get(key))
        values.append(row.get(module.GROUP_DATE_MARKER[group]))
    return tuple(values)


def _group_changed(module: Any, entry: dict[str, Any], row: dict[str, Any], group: str) -> bool:
    for key in module.GROUP_KEYS[group]:
        if key not in row:
            continue
        value = row.get(key)
        if value in (None, ""):
            continue
        if entry.get(key) != value:
            return True
    source_date = module._group_date(row, group)
    marker = module.GROUP_DATE_MARKER[group]
    return bool(source_date and entry.get(marker) != source_date)


def _update_hold_entry(module: Any, state: Any, code: str, row: dict[str, Any], session: dict[str, Any]) -> bool:
    token_cache = getattr(state, "session_metric_fast_tokens", None)
    if not isinstance(token_cache, dict):
        token_cache = {}
        state.session_metric_fast_tokens = token_cache

    token = _row_token(module, row, session)
    if token_cache.get(code) == token:
        status = getattr(state, "status", None)
        if isinstance(status, dict):
            status["session_metric_fast_skip_count"] = int(
                status.get("session_metric_fast_skip_count") or 0
            ) + 1
        return False
    token_cache[code] = token

    cache = module._ensure_hold_cache(state)
    entry = dict(cache.get(code) or {"stock_code": code})
    current_date = _date_digits(session.get("trading_date") or session.get("calendar_date"))
    started = module._session_started(session)
    changed = False

    for group in ("orderbook", "execution", "strength5"):
        if not module._group_usable(row, group):
            continue
        if _group_changed(module, entry, row, group):
            module._copy_group(entry, row, group)
            changed = True

    for group in ("program", "large_trade"):
        if not module._group_usable(row, group):
            continue
        source_date = module._group_date(row, group)
        if started and (not source_date or source_date != current_date):
            continue
        if _group_changed(module, entry, row, group):
            module._copy_group(entry, row, group)
            changed = True

    if changed:
        entry["stock_code"] = code
        entry["saved_at"] = module.now_text()
        cache[code] = entry
        state.session_metric_hold_dirty = True
        status = getattr(state, "status", None)
        if isinstance(status, dict):
            status["session_metric_fast_update_count"] = int(
                status.get("session_metric_fast_update_count") or 0
            ) + 1
    return changed


def install(module: Any) -> None:
    if getattr(module, "_stockboard_session_metric_fast_installed", False):
        return

    module._date_digits = _date_digits
    module._numeric = lambda value: _numeric(module, value)
    module._positive = lambda value: _positive(module, value)
    module._update_hold_entry = lambda state, code, row, session: _update_hold_entry(
        module, state, code, row, session
    )
    module._stockboard_session_metric_fast_installed = True
