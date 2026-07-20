from __future__ import annotations

"""Serialize previous-close hold overlays and suppress unverified seed rows.

The continuity overlay runs at most once per two seconds and touches only the
existing in-memory rows. Holding the State RLock around the complete overlay
prevents a just-accepted current-day trade from being replaced by the previous
close between the live-code scan and row update.

When a portable candidate covers only part of the universe, codes without an
exact row must not retain query-time or seed display values. Those codes keep
identity fields such as stock code/name but their market display fields are
removed until an exact previous-close row or an accepted current-day trade is
available.

No source, request, loop, render cadence, or browser calculation is added.
"""

from typing import Any

from realtime_v2.common import normalize_code, to_number

PATCH_VERSION = "board_display_continuity_rlock_v2"

_DISPLAY_FIELDS = (
    "price",
    "trade_price",
    "change_rate",
    "trade_value_eok",
    "amount_ratio",
    "ohlc",
    "day_open",
    "day_high",
    "day_low",
    "day_close",
    "prev_trade_value_eok",
    "prev_trade_value_date",
    "prev_rank",
    "source_code",
    "row_source",
    "source_trading_date",
    "price_trading_date",
    "change_rate_trading_date",
    "trade_value_trading_date",
    "ohlc_trading_date",
    "market_scope",
    "portable_board_quality",
    "portable_parser_version",
    "portable_board_generation",
    "portable_board_applied_at",
)


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _valid_exact_codes(continuity, guard_module, payload: Any, source_date: str) -> set[str]:
    if not isinstance(payload, dict):
        return set()
    board_values = payload.get("board_values")
    if not isinstance(board_values, dict):
        return set()

    result: set[str] = set()
    for raw_code, raw_value in board_values.items():
        code = normalize_code(raw_code)
        if not code or not isinstance(raw_value, dict):
            continue
        if raw_value.get("portable_parser_version") != guard_module.PORTABLE_PARSER_VERSION:
            continue
        if any(
            _date_digits(raw_value.get(key)) != source_date
            for key in (
                "source_trading_date",
                "price_trading_date",
                "change_rate_trading_date",
                "trade_value_trading_date",
                "ohlc_trading_date",
            )
        ):
            continue
        price = continuity._positive(raw_value.get("price"))
        change_rate = to_number(raw_value.get("change_rate"))
        trade_value = to_number(raw_value.get("trade_value_eok"))
        ohlc = raw_value.get("ohlc")
        if price is None or change_rate is None or trade_value is None or not isinstance(ohlc, dict):
            continue
        result.add(code)
    return result


def _suppress_unverified_seed_rows(
    continuity,
    guard_module,
    state,
    payload: Any,
    *,
    source_date: str,
    current_date: str | None,
    hold_only: bool,
) -> int:
    valid_exact = _valid_exact_codes(continuity, guard_module, payload, source_date)
    live_codes = (
        continuity._current_live_codes(state, current_date or "")
        if hold_only and current_date
        else set()
    )
    suppressed = 0
    universe_codes = list(getattr(state, "seed_rank_by_code", {}) or state.quotes)
    for raw_code in universe_codes:
        code = normalize_code(raw_code)
        if not code or code in valid_exact or code in live_codes:
            continue
        quote = state._quote(code)
        for key in _DISPLAY_FIELDS:
            quote.pop(key, None)
        quote["portable_board_missing"] = True
        suppressed += 1
    state.status["board_display_seed_suppressed_count"] = suppressed
    state.status["board_display_continuity_safety_version"] = PATCH_VERSION
    return suppressed


def install(base) -> None:
    from realtime_v2 import worker_board_display_continuity_patch as continuity

    if getattr(continuity, "_display_continuity_rlock_installed", False):
        return

    original_apply_payload = continuity._apply_payload

    def apply_payload_locked(
        guard_module,
        state,
        payload: dict[str, Any],
        **kwargs: Any,
    ):
        lock = getattr(state, "lock", None)
        if lock is None:
            return original_apply_payload(guard_module, state, payload, **kwargs)
        with lock:
            result = original_apply_payload(guard_module, state, payload, **kwargs)
            _suppress_unverified_seed_rows(
                continuity,
                guard_module,
                state,
                payload,
                source_date=_date_digits(kwargs.get("source_date")),
                current_date=_date_digits(kwargs.get("current_date")),
                hold_only=bool(kwargs.get("hold_only")),
            )
            return result

    continuity._apply_payload = apply_payload_locked
    continuity._display_continuity_rlock_installed = True
    continuity._display_continuity_rlock_version = PATCH_VERSION

    state_class = getattr(base, "State", None)
    if state_class is not None:
        state_class._stockboard_display_continuity_rlock_installed = True
        state_class._stockboard_display_continuity_rlock_version = PATCH_VERSION
