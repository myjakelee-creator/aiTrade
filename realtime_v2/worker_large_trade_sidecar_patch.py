from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import (
    LARGE_TRADE_THRESHOLD_KRW,
    normalize_code,
    now_text,
    to_int,
    to_number,
    trading_date_text,
)

_PERSIST_KEYS = (
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
    "_metric_continuity_large_trade_date",
    "_session_hold_large_trade_date",
)


def _number(value: Any, default: float = 0.0) -> float:
    number = to_number(value)
    return default if number is None else float(number)


def _integer(value: Any, default: int = 0) -> int:
    number = to_int(value)
    return default if number is None else int(number)


def install(base) -> None:
    """Apply sparse large-trade delta events from the isolated 32-bit sidecar.

    The sidecar sends only qualifying >= 50 million KRW events. This worker patch
    does not add a thread, TR, FID, browser calculation, or price-field mutation.
    """

    state_class = getattr(base, "State", None)
    if state_class is None:
        return
    if getattr(state_class, "_stockboard_large_trade_sidecar_installed", False):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *_PERSIST_KEYS))
    )
    original_apply_event = state_class.apply_event

    def apply_large_trade_delta(self, event: dict[str, Any]) -> None:
        values = base.merged_event_values(event)
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
        )
        if not code:
            return

        buy_count_delta = max(0, _integer(values.get("large_trade_buy_count_delta")))
        sell_count_delta = max(0, _integer(values.get("large_trade_sell_count_delta")))
        buy_sum_delta = max(0.0, _number(values.get("large_trade_buy_sum_eok_delta")))
        sell_sum_delta = max(0.0, _number(values.get("large_trade_sell_sum_eok_delta")))
        if not any(
            value > 0
            for value in (
                buy_count_delta,
                sell_count_delta,
                buy_sum_delta,
                sell_sum_delta,
            )
        ):
            return

        updated_at = (
            values.get("large_trade_updated_at")
            or event.get("ts")
            or now_text()
        )
        trade_date = str(values.get("trading_date") or trading_date_text())
        with self.lock:
            quote = self._quote(code)
            quote["large_trade_buy_count"] = _integer(
                quote.get("large_trade_buy_count")
            ) + buy_count_delta
            quote["large_trade_sell_count"] = _integer(
                quote.get("large_trade_sell_count")
            ) + sell_count_delta
            quote["large_trade_buy_sum_eok"] = round(
                _number(quote.get("large_trade_buy_sum_eok")) + buy_sum_delta,
                4,
            )
            quote["large_trade_sell_sum_eok"] = round(
                _number(quote.get("large_trade_sell_sum_eok")) + sell_sum_delta,
                4,
            )
            quote["large_trade_net_count"] = (
                quote["large_trade_buy_count"] - quote["large_trade_sell_count"]
            )
            quote["large_trade_net_sum_eok"] = round(
                quote["large_trade_buy_sum_eok"]
                - quote["large_trade_sell_sum_eok"],
                4,
            )
            quote["large_trade_source"] = str(
                values.get("large_trade_source") or "fid15_sidecar"
            )
            quote["large_trade_status"] = str(
                values.get("large_trade_status") or "ok"
            )
            quote["large_trade_threshold_krw"] = _integer(
                values.get("large_trade_threshold_krw"),
                LARGE_TRADE_THRESHOLD_KRW,
            )
            quote["large_trade_updated_at"] = updated_at
            quote["large_trade_display_basis"] = "current_session_sidecar"
            quote["large_trade_available"] = True
            quote["_metric_continuity_large_trade_date"] = trade_date
            quote["_session_hold_large_trade_date"] = trade_date

            entry = self.daily_values_by_code.setdefault(code, {})
            for key in _PERSIST_KEYS:
                if key in quote:
                    entry[key] = deepcopy(quote.get(key))
            self.status["large_trade_sidecar_event_count"] = int(
                self.status.get("large_trade_sidecar_event_count") or 0
            ) + 1
            self.status["large_trade_sidecar_last_code"] = code
            self.status["large_trade_sidecar_last_at"] = updated_at
            self.status["large_trade_sidecar_last_error"] = None
            self.status["large_trade_sidecar_policy"] = (
                "isolated_qax; opening_top20; normal_top100; qualifying_events_only"
            )
            self._mark_daily_dirty()

    def apply_event(self, event: dict[str, Any]) -> None:
        if event.get("type") != "large_trade_delta":
            return original_apply_event(self, event)

        # Preserve the worker's normal event counters and event-log trigger. The
        # original method ignores unknown event types after updating those counters.
        original_apply_event(self, event)
        try:
            apply_large_trade_delta(self, event)
        except Exception as error:
            with self.lock:
                self.status["large_trade_sidecar_last_error"] = (
                    f"{type(error).__name__}: {error}"
                )

    state_class.apply_event = apply_event
    state_class._stockboard_large_trade_sidecar_installed = True
