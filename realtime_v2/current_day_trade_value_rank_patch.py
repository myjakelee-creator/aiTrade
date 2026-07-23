from __future__ import annotations

"""Exclude previous-day HOLD amounts from the current-day trade-value ranking.

The display-continuity layer intentionally keeps verified previous-close rows visible
until a current-day trade arrives. Those held amounts are useful as continuity data but
must not compete with current-day cumulative trade value. This output-only patch asks
the existing rows method for the full universe, ranks rows whose cumulative value is
explicitly tagged with the current trading date, and moves held/unknown rows after them.

No QAx, FID, request, thread, timer, SSE cadence, or collector behavior changes.
"""

from copy import deepcopy
from typing import Any

PATCH_VERSION = "current_day_trade_value_rank_v1"


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _current_date(state) -> str:
    status = getattr(state, "status", {})
    if not isinstance(status, dict):
        return ""
    for key in (
        "market_trading_date",
        "board_display_current_trading_date",
        "board_expected_trading_date",
    ):
        date_text = _date_digits(status.get(key))
        if date_text:
            return date_text
    return ""


def _row_trade_value_date(row: dict[str, Any]) -> str:
    for key in ("trade_value_trading_date", "source_trading_date"):
        date_text = _date_digits(row.get(key))
        if date_text:
            return date_text
    return ""


def _requested_limit(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
    candidate = kwargs.get("limit")
    if candidate is None and args:
        candidate = args[0]
    try:
        return max(1, min(300, int(candidate or 300)))
    except (TypeError, ValueError):
        return 300


def install(base) -> None:
    state_class = getattr(base, "State", None)
    if (
        state_class is None
        or not callable(getattr(state_class, "rows", None))
        or getattr(state_class, "_stockboard_current_day_trade_value_rank_installed", False)
    ):
        return

    original_rows = state_class.rows

    def rows(self, *args, **kwargs):
        limit = _requested_limit(args, kwargs)
        all_rows = original_rows(self, 300)
        if not isinstance(all_rows, list):
            return all_rows

        current_date = _current_date(self)
        if not current_date:
            return all_rows[:limit]

        eligible: list[dict[str, Any]] = []
        held: list[dict[str, Any]] = []
        for raw_row in all_rows:
            if not isinstance(raw_row, dict):
                continue
            row = deepcopy(raw_row)
            value_date = _row_trade_value_date(row)
            value = _number(row.get("trade_value_eok"))
            if value_date == current_date and value is not None and value >= 0:
                row["current_day_trade_value_eligible"] = True
                eligible.append(row)
                continue

            row["current_day_trade_value_eligible"] = False
            row["held_trade_value_eok"] = row.get("trade_value_eok")
            row["trade_value_eok"] = None
            row["amount_ratio"] = None
            row["amount_ratio_missing_reason"] = "current_day_trade_value_not_ready"
            row["rank"] = None
            held.append(row)

        eligible.sort(
            key=lambda row: (
                -float(_number(row.get("trade_value_eok")) or 0.0),
                str(row.get("stock_code") or ""),
            )
        )
        for rank, row in enumerate(eligible, start=1):
            row["rank"] = rank

        held.sort(
            key=lambda row: (
                _number(row.get("prev_rank"))
                if _number(row.get("prev_rank")) is not None
                else 999999.0,
                str(row.get("stock_code") or ""),
            )
        )

        with self.lock:
            self.status["current_day_trade_value_rank_version"] = PATCH_VERSION
            self.status["current_day_trade_value_rank_eligible_count"] = len(eligible)
            self.status["current_day_trade_value_rank_held_count"] = len(held)

        return (eligible + held)[:limit]

    state_class.rows = rows
    state_class._stockboard_current_day_trade_value_rank_installed = True
    state_class._stockboard_current_day_trade_value_rank_version = PATCH_VERSION
