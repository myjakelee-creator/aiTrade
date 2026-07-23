from __future__ import annotations

"""Keep the final live board after the calendar-defined market close.

This patch is display-only. It adds no QAx/FID/REST/WebSocket work and does not
change collector, worker trade acceptance, SSE cadence, or active-session rows.

Policy:
- when the worker has actually processed current completed-day trades, keep the
  in-memory board after ``market_session`` changes to a closed phase;
- continue accepting late collector events because State.apply_event remains
  untouched;
- only fall back to the existing verified portable exact-close path when this
  process has no accepted trades for the completed trading date (restart/new
  connection/no-data case).
"""

from datetime import datetime
from typing import Any

PATCH_VERSION = "after_close_live_hold_v1"
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


def _current_day_live_count(state, target_date: str) -> int:
    if not target_date:
        return 0
    count = 0
    with state.lock:
        quotes = getattr(state, "quotes", {}) or {}
        for quote in quotes.values():
            if not isinstance(quote, dict):
                continue
            price = _number(quote.get("price") or quote.get("trade_price"))
            trade_value = _number(quote.get("trade_value_eok"))
            if price is None or price <= 0 or trade_value is None:
                continue
            if any(_date_digits(quote.get(key)) == target_date for key in _DATE_FIELDS):
                count += 1
    return count


def _has_accepted_current_day_trades(state, target_date: str) -> tuple[bool, int]:
    with state.lock:
        trade_count = int(getattr(state, "status", {}).get("trade_count") or 0)
    live_count = _current_day_live_count(state, target_date)
    return trade_count > 0 and live_count > 0, live_count


def install(base) -> None:
    from realtime_v2 import worker_board_trading_date_guard as guard_module

    guard_class = guard_module.PortableBoardGuard
    if getattr(guard_class, "_stockboard_after_close_live_hold_installed", False):
        return

    original_apply = guard_class.apply

    def apply(self, state, now: datetime | None = None):
        current = now or datetime.now()
        target_date, phase, active = guard_module.board_target_context(current)
        if not active:
            live_ready, live_count = _has_accepted_current_day_trades(state, target_date)
            if live_ready:
                with state.lock:
                    state.status.update(
                        {
                            "after_close_live_hold_version": PATCH_VERSION,
                            "after_close_live_hold_active": True,
                            "after_close_live_hold_target_date": target_date or None,
                            "after_close_live_hold_phase": phase,
                            "after_close_live_hold_row_count": live_count,
                            "after_close_live_hold_basis": "accepted_worker_trades",
                            "board_display_basis": "in_memory_after_close_live_hold",
                            "board_expected_trading_date": target_date or None,
                            "board_market_phase": phase,
                        }
                    )
                self._applied_result = True
                return True

        result = original_apply(self, state, current)
        with state.lock:
            state.status.update(
                {
                    "after_close_live_hold_version": PATCH_VERSION,
                    "after_close_live_hold_active": False,
                    "after_close_live_hold_target_date": target_date or None,
                    "after_close_live_hold_phase": phase,
                    "after_close_live_hold_row_count": 0,
                    "after_close_live_hold_basis": (
                        "active_session" if active else "portable_exact_fallback"
                    ),
                }
            )
        return result

    guard_class.apply = apply
    guard_class._stockboard_after_close_live_hold_installed = True
    guard_class._stockboard_after_close_live_hold_version = PATCH_VERSION

    state_class = getattr(base, "State", None)
    if state_class is not None:
        state_class._stockboard_after_close_live_hold_installed = True
        state_class._stockboard_after_close_live_hold_version = PATCH_VERSION


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_after_close_live_hold_install_wrapped", False):
        return

    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        install(base)

    opening_module.install = install_after_opening
    opening_module._after_close_live_hold_install_wrapped = True
