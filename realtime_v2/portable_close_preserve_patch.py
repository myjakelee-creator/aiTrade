from __future__ import annotations

"""Preserve accepted close rows when the portable fallback is missing or partial.

The portable exact-close snapshot is a fallback only. It must never erase accepted
20:00 in-memory/checkpoint values and must never turn a populated board into an
empty list merely because the portable snapshot is unavailable.

No collector, QAx, FID, REST/WebSocket request, timer, thread, or SSE cadence
changes are introduced here.
"""

from copy import deepcopy
from typing import Any

PATCH_VERSION = "portable_close_preserve_v1"
_APPLY_MARKER = "_stockboard_portable_close_preserve_apply_wrapper"
_DATE_FIELDS = (
    "received_at",
    "price_received_at",
    "trade_received_at",
    "last_trade_event_received_at",
    "source_trading_date",
    "price_trading_date",
    "trade_value_trading_date",
)


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


def _usable_quote(quote: Any, target_date: str = "") -> bool:
    if not isinstance(quote, dict):
        return False
    price = _number(quote.get("price") or quote.get("trade_price"))
    trade_value = _number(quote.get("trade_value_eok"))
    if price is None or price <= 0 or trade_value is None or trade_value < 0:
        return False
    if not target_date:
        return True
    dates = {_date_digits(quote.get(key)) for key in _DATE_FIELDS}
    dates.discard("")
    if not dates:
        return True
    return target_date in dates


def _snapshot_usable_quotes(state, target_date: str) -> dict[str, dict[str, Any]]:
    with state.lock:
        quotes = getattr(state, "quotes", {}) or {}
        return {
            str(code): deepcopy(quote)
            for code, quote in quotes.items()
            if _usable_quote(quote, target_date)
        }


def _restore_removed_or_degraded(state, preserved: dict[str, dict[str, Any]], target_date: str) -> int:
    restored = 0
    with state.lock:
        quotes = getattr(state, "quotes", {}) or {}
        for code, previous in preserved.items():
            current = quotes.get(code)
            if _usable_quote(current, target_date):
                continue
            quote = state._quote(code)
            quote.clear()
            quote.update(deepcopy(previous))
            quote["portable_fallback_preserved"] = True
            quote["portable_fallback_preserve_version"] = PATCH_VERSION
            restored += 1
    return restored


def install(base) -> None:
    from realtime_v2 import worker_board_trading_date_guard as guard_module

    guard_class = guard_module.PortableBoardGuard
    current_apply = guard_class.apply
    if getattr(current_apply, _APPLY_MARKER, False):
        return

    original_apply = current_apply

    def apply(self, state, now=None):
        target_date, phase, active = guard_module.board_target_context(now)
        if active:
            return original_apply(self, state, now)

        preserved = _snapshot_usable_quotes(state, target_date)
        if preserved:
            with state.lock:
                state.status.update(
                    {
                        "portable_close_preserve_version": PATCH_VERSION,
                        "portable_close_preserve_active": True,
                        "portable_close_preserve_phase": phase,
                        "portable_close_preserve_target_date": target_date or None,
                        "portable_close_preserve_row_count": len(preserved),
                        "portable_close_preserve_restored_count": 0,
                        "board_display_basis": "preserved_live_close_before_portable_fallback",
                        "board_expected_trading_date": target_date or None,
                        "board_market_phase": phase,
                    }
                )
            self._applied_result = True
            return True

        before = {}
        with state.lock:
            for code, quote in (getattr(state, "quotes", {}) or {}).items():
                if _usable_quote(quote):
                    before[str(code)] = deepcopy(quote)

        result = original_apply(self, state, now)
        restored = _restore_removed_or_degraded(state, before, target_date)
        usable_after = _snapshot_usable_quotes(state, target_date)

        if not result and (restored or usable_after):
            result = True
            self._applied_result = True

        with state.lock:
            state.status.update(
                {
                    "portable_close_preserve_version": PATCH_VERSION,
                    "portable_close_preserve_active": bool(restored or usable_after),
                    "portable_close_preserve_phase": phase,
                    "portable_close_preserve_target_date": target_date or None,
                    "portable_close_preserve_row_count": len(usable_after),
                    "portable_close_preserve_restored_count": restored,
                    "board_display_basis": (
                        "preserved_close_after_portable_fallback"
                        if restored
                        else state.status.get("board_display_basis")
                    ),
                }
            )
        return result

    setattr(apply, _APPLY_MARKER, True)
    setattr(apply, "_stockboard_portable_close_preserve_version", PATCH_VERSION)
    guard_class.apply = apply
    guard_class._stockboard_portable_close_preserve_installed = True
    guard_class._stockboard_portable_close_preserve_version = PATCH_VERSION


__all__ = ["PATCH_VERSION", "install"]
