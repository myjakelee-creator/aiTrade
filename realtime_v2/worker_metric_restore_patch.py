from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, to_number, trading_date_text

PROGRAM_KEYS = (
    "program_net",
    "program_net_updated_at",
    "program_net_source",
    "program_net_status",
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
    "large_trade_threshold_krw",
    "large_trade_updated_at",
)
LARGE_TRADE_NUMERIC_KEYS = LARGE_TRADE_KEYS[:6]
WAIT_SOURCES = {
    "",
    "new_session_wait",
    "new_session_reset",
    "price_only_collector_no_fid15",
    "disabled_in_production_price_collector",
    "unavailable_no_current_source",
}
PROGRAM_DATE_KEYS = (
    "_metric_continuity_program_date",
    "_session_hold_program_date",
    "_source_trading_date",
    "source_trading_date",
    "trading_date",
    "program_net_updated_at",
)
LARGE_TRADE_DATE_KEYS = (
    "_metric_continuity_large_trade_date",
    "_session_hold_large_trade_date",
    "_source_trading_date",
    "source_trading_date",
    "trading_date",
    "large_trade_updated_at",
)


def _source_text(source: dict[str, Any], key: str) -> str:
    return str(source.get(key) or "").strip()


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _metric_date(source: dict[str, Any], group: str) -> str:
    keys = PROGRAM_DATE_KEYS if group == "program" else LARGE_TRADE_DATE_KEYS
    for key in keys:
        parsed = _date_digits(source.get(key))
        if parsed:
            return parsed
    return ""


def _program_valid(source: dict[str, Any]) -> bool:
    if not isinstance(source, dict) or "program_net" not in source:
        return False
    value = to_number(source.get("program_net"))
    if value is None:
        return False
    source_name = _source_text(source, "program_net_source")
    status = _source_text(source, "program_net_status")
    basis = _source_text(source, "program_net_display_basis")
    if source_name in WAIT_SOURCES or status in {
        "new_session_wait",
        "unavailable_waiting_refresh",
    }:
        return False
    if basis in {"previous_session_hold", "last_session_hold"}:
        return False
    return bool(source.get("program_net_updated_at") or source_name or status)


def _large_trade_valid(source: dict[str, Any]) -> bool:
    if not isinstance(source, dict):
        return False
    values = [to_number(source.get(key)) for key in LARGE_TRADE_NUMERIC_KEYS]
    if any(value not in (None, 0, 0.0) for value in values):
        return True
    source_name = _source_text(source, "large_trade_source")
    status = _source_text(source, "large_trade_status")
    basis = _source_text(source, "large_trade_display_basis")
    if source_name in WAIT_SOURCES or status in {
        "new_session_wait",
        "new_session_reset",
        "unavailable_no_current_source",
    }:
        return False
    if basis in {"previous_session_hold", "last_session_hold"}:
        return False
    return bool(
        source.get("large_trade_updated_at")
        or source_name
        or status in {"ok", "cached", "cached_current_session", "current_session"}
    )


def _current_source(
    source: dict[str, Any],
    group: str,
    current_date: str,
    *,
    trust_current_daily: bool = False,
) -> bool:
    valid = _program_valid(source) if group == "program" else _large_trade_valid(source)
    if not valid:
        return False
    if trust_current_daily:
        return True
    source_date = _metric_date(source, group)
    return not source_date or source_date == current_date


def _copy_present(target: dict[str, Any], source: dict[str, Any], keys: tuple[str, ...]) -> int:
    copied = 0
    for key in keys:
        if key not in source:
            continue
        value = source.get(key)
        if value in (None, ""):
            continue
        target[key] = deepcopy(value)
        copied += 1
    return copied


def _first_current_source(
    sources: list[tuple[dict[str, Any], bool]],
    group: str,
    current_date: str,
) -> dict[str, Any] | None:
    for source, trust_current_daily in sources:
        if _current_source(
            source,
            group,
            current_date,
            trust_current_daily=trust_current_daily,
        ):
            return source
    return None


def _restore_metric_row(
    row: dict[str, Any],
    sources: list[tuple[dict[str, Any], bool]],
    current_date: str,
) -> tuple[bool, bool, int]:
    copied = 0

    if not _current_source(row, "program", current_date):
        source = _first_current_source(sources, "program", current_date)
        if source is not None:
            copied += _copy_present(row, source, PROGRAM_KEYS)
            row["program_net_display_basis"] = "same_day_cache"
            row["program_net_cache_restored"] = True

    program_available = _current_source(row, "program", current_date)
    row["program_net_available"] = program_available
    if not program_available:
        # The browser converts null to zero. Omitting the key correctly displays '-'.
        row.pop("program_net", None)
        row["program_net_source"] = "background_refresh_pending"
        row["program_net_status"] = "unavailable_waiting_refresh"
        row["program_net_display_basis"] = "unavailable"

    if not _current_source(row, "large_trade", current_date):
        source = _first_current_source(sources, "large_trade", current_date)
        if source is not None:
            copied += _copy_present(row, source, LARGE_TRADE_KEYS)
            row["large_trade_source"] = (
                row.get("large_trade_source") or "same_day_cache"
            )
            row["large_trade_status"] = (
                row.get("large_trade_status") or "cached_current_session"
            )
            row["large_trade_display_basis"] = "same_day_cache"
            row["large_trade_cache_restored"] = True

    large_trade_available = _current_source(row, "large_trade", current_date)
    row["large_trade_available"] = large_trade_available
    if not large_trade_available:
        # Exact live large-trade accumulation needs signed FID15, which remains
        # intentionally absent from the stable price collector. Never fake zero.
        for key in LARGE_TRADE_NUMERIC_KEYS:
            row.pop(key, None)
        row["large_trade_source"] = "price_only_collector_no_fid15"
        row["large_trade_status"] = "unavailable_no_current_source"
        row["large_trade_display_basis"] = "unavailable"

    if copied:
        row["metric_restore_applied"] = True
        row["metric_restore_applied_count"] = copied
    return program_available, large_trade_available, copied


def install(base) -> None:
    """Restore program/large-trade display without touching startup or price paths.

    Program net keeps the existing stable background updater cadence. This patch
    never invokes a REST/TR request at worker startup. Large trade restores only
    same-day persisted aggregates from already existing caches; no FID, TR, thread,
    socket, or browser work is added.
    """

    state_class = getattr(base, "State", None)
    if state_class is None:
        return
    if getattr(state_class, "_stockboard_metric_restore_installed", False):
        return

    original_apply_program = state_class.apply_program_net_values
    original_rows = state_class.rows

    def apply_program_net_values(self, values, source, status):
        updated = original_apply_program(self, values, source, status)
        if updated:
            rebuild = getattr(self, "request_background_rebuild", None)
            if callable(rebuild):
                try:
                    rebuild(reason="program_net_update", force=True)
                except Exception as error:
                    with self.lock:
                        self.status["program_net_rebuild_error"] = str(error)
        return updated

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        codes = {
            normalize_code(row.get("stock_code"))
            for row in result
            if isinstance(row, dict)
        }
        codes.discard("")
        with self.lock:
            daily_by_code = {
                code: dict(self.daily_values_by_code.get(code) or {})
                for code in codes
            }
            session_hold = getattr(self, "session_metric_hold_by_code", {})
            session_hold_by_code = {
                code: dict(session_hold.get(code) or {})
                for code in codes
                if isinstance(session_hold, dict)
            }
            continuity = getattr(self, "board_metric_continuity_by_code", {})
            continuity_by_code = {
                code: dict(continuity.get(code) or {})
                for code in codes
                if isinstance(continuity, dict)
            }

        current_date = str(trading_date_text())
        program_available_count = 0
        large_trade_available_count = 0
        restored_rows = 0
        restored_fields = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            sources = [
                (daily_by_code.get(code, {}), True),
                (session_hold_by_code.get(code, {}), False),
                (continuity_by_code.get(code, {}), False),
            ]
            program_ok, large_ok, copied = _restore_metric_row(
                row,
                sources,
                current_date,
            )
            program_available_count += int(program_ok)
            large_trade_available_count += int(large_ok)
            if copied:
                restored_rows += 1
                restored_fields += copied

        with self.lock:
            self.status["program_net_display_available_count"] = program_available_count
            self.status["large_trade_display_available_count"] = large_trade_available_count
            self.status["metric_restore_applied_rows"] = restored_rows
            self.status["metric_restore_applied_fields"] = restored_fields
            self.status["program_net_refresh_policy"] = (
                "existing_background_updater; no startup REST/TR request"
            )
            self.status["large_trade_live_policy"] = (
                "same_day_persisted_only; price collector remains FID15-free"
            )
        return result

    state_class.apply_program_net_values = apply_program_net_values
    state_class.rows = rows
    state_class._stockboard_metric_restore_installed = True
