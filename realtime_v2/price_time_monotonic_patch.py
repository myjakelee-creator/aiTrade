from __future__ import annotations

"""Keep live price/rate timestamps monotonic while preserving cumulative-field handling.

The SOR guard intentionally lets price/rate survive same-day FID20 interleaving, but
collector coalescing can deliver an older event after a newer event. In that case the
existing guard may correctly preserve cumulative fields while the base worker overwrites
price, rate, and received_at with the older event.

This wrapper runs outside the existing cumulative guard. It lets the guarded apply path
process every event, then restores only the decision-critical price fields when the event
receive timestamp is older than the quote's already accepted receive timestamp.

No collector, QAx, FID, request, thread, timer, ranking, orderbook, or SSE cadence changes.
"""

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from realtime_v2.common import normalize_code

PATCH_VERSION = "price_time_monotonic_v1"
_REASON = "event_received_at_older_than_quote"
_PRICE_FIELDS = (
    "price",
    "trade_price",
    "change_rate",
    "trade_time",
    "received_at",
    "price_age_sec",
    "fid20_lag_sec",
    "_trade_time_seconds",
    "market_type_raw",
    "source_code",
)


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _merged_values(base, event: dict[str, Any]) -> dict[str, Any]:
    try:
        values = base.merged_event_values(event)
    except Exception:
        values = {}
    return values if isinstance(values, dict) else {}


def _event_received_at(event: dict[str, Any], values: dict[str, Any]) -> Any:
    return (
        event.get("ts")
        or values.get("price_received_at")
        or values.get("trade_received_at")
        or values.get("received_at")
    )


def _restore_fields(quote: dict[str, Any], saved: dict[str, tuple[bool, Any]]) -> None:
    for key, (existed, value) in saved.items():
        if existed:
            quote[key] = deepcopy(value)
        else:
            quote.pop(key, None)


def _install_state_wrapper(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_price_time_monotonic_installed", False
    ):
        return

    guarded_apply = state_class._apply_trade

    def apply_trade(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return guarded_apply(self, event)

        values = _merged_values(base, event)
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return guarded_apply(self, event)

        incoming_text = _event_received_at(event, values)
        incoming_ts = _timestamp(incoming_text)
        saved: dict[str, tuple[bool, Any]] = {}
        current_text = None
        current_ts = None

        with self.lock:
            quote = self.quotes.get(code)
            if isinstance(quote, dict):
                current_text = quote.get("received_at")
                current_ts = _timestamp(current_text)
                if (
                    incoming_ts is not None
                    and current_ts is not None
                    and incoming_ts < current_ts
                ):
                    saved = {
                        key: (key in quote, deepcopy(quote.get(key)))
                        for key in _PRICE_FIELDS
                    }

        result = guarded_apply(self, event)
        if not saved:
            return result

        with self.lock:
            quote = self.quotes.get(code)
            if not isinstance(quote, dict):
                return result
            _restore_fields(quote, saved)
            self.status["price_time_monotonic_version"] = PATCH_VERSION
            self.status["stale_price_event_suppressed_count"] = int(
                self.status.get("stale_price_event_suppressed_count") or 0
            ) + 1
            self.status["last_stale_price_event_suppressed"] = {
                "stock_code": code,
                "reason": _REASON,
                "incoming_received_at": incoming_text,
                "kept_received_at": current_text,
            }
        return result

    state_class._apply_trade = apply_trade
    state_class._stockboard_price_time_monotonic_installed = True
    state_class._stockboard_price_time_monotonic_version = PATCH_VERSION


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_trade_field_regression_guard as guard_module

    if getattr(guard_module, "_price_time_monotonic_install_wrapped", False):
        return
    original_install = guard_module.install

    def install(base) -> None:
        original_install(base)
        _install_state_wrapper(base)

    guard_module.install = install
    guard_module._price_time_monotonic_install_wrapped = True
