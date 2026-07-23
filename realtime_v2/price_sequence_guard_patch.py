from __future__ import annotations

"""Protect only live price/rate fields with Collector callback arrival sequence.

Collector events carry ``collector_price_epoch`` and ``collector_price_seq``.  The
worker accepts price/rate from a newer sequence within the same epoch.  When an older
sequence arrives late (for example after a socket retry), cumulative fields still pass
through the existing trade-field regression guard, but decision-critical price fields
are restored to the last accepted values.

Missing sequence metadata is fail-open for compatibility with fixtures and non-QAx
adapters.  A new Collector epoch resets the per-symbol sequence naturally.
"""

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code

PATCH_VERSION = "price_sequence_guard_v1"
_REASON = "collector_price_seq_not_newer"
_PRICE_FIELDS = (
    "price",
    "trade_price",
    "change_rate",
    "received_at",
    "price_age_sec",
    "market_type_raw",
    "source_code",
)


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _merged_values(base, event: dict[str, Any]) -> dict[str, Any]:
    try:
        values = base.merged_event_values(event)
    except Exception:
        values = {}
    return values if isinstance(values, dict) else {}


def _sequence(event: dict[str, Any], values: dict[str, Any]) -> tuple[str | None, int | None]:
    epoch = event.get("collector_price_epoch") or values.get("collector_price_epoch")
    seq = _to_int(event.get("collector_price_seq") or values.get("collector_price_seq"))
    epoch_text = str(epoch).strip() if epoch not in (None, "") else None
    return epoch_text, seq


def _restore_fields(quote: dict[str, Any], saved: dict[str, tuple[bool, Any]]) -> None:
    for key, (existed, value) in saved.items():
        if existed:
            quote[key] = deepcopy(value)
        else:
            quote.pop(key, None)


def _install_state_wrapper(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_price_sequence_guard_installed", False
    ):
        return

    guarded_apply = state_class._apply_trade

    def apply_trade(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return guarded_apply(self, event)

        values = _merged_values(base, event)
        epoch, seq = _sequence(event, values)
        if epoch is None or seq is None:
            return guarded_apply(self, event)

        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return guarded_apply(self, event)

        saved: dict[str, tuple[bool, Any]] = {}
        last_epoch = None
        last_seq = None
        with self.lock:
            quote = self.quotes.get(code)
            if isinstance(quote, dict):
                last_epoch = quote.get("collector_price_epoch")
                last_seq = _to_int(quote.get("collector_price_seq"))
                if last_epoch == epoch and last_seq is not None and seq <= last_seq:
                    saved = {
                        key: (key in quote, deepcopy(quote.get(key)))
                        for key in _PRICE_FIELDS
                    }

        result = guarded_apply(self, event)

        with self.lock:
            quote = self.quotes.get(code)
            if not isinstance(quote, dict):
                return result
            if saved:
                _restore_fields(quote, saved)
                self.status["price_sequence_suppressed_count"] = int(
                    self.status.get("price_sequence_suppressed_count") or 0
                ) + 1
                self.status["last_price_sequence_suppressed"] = {
                    "stock_code": code,
                    "reason": _REASON,
                    "incoming_epoch": epoch,
                    "incoming_seq": seq,
                    "kept_epoch": last_epoch,
                    "kept_seq": last_seq,
                }
            else:
                quote["collector_price_epoch"] = epoch
                quote["collector_price_seq"] = seq
                self.status["price_sequence_accepted_count"] = int(
                    self.status.get("price_sequence_accepted_count") or 0
                ) + 1
                self.status["last_price_sequence_accepted"] = {
                    "stock_code": code,
                    "collector_price_epoch": epoch,
                    "collector_price_seq": seq,
                }
            self.status["price_sequence_guard_version"] = PATCH_VERSION
        return result

    state_class._apply_trade = apply_trade
    state_class._stockboard_price_sequence_guard_installed = True
    state_class._stockboard_price_sequence_guard_version = PATCH_VERSION


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_trade_field_regression_guard as guard_module

    if getattr(guard_module, "_price_sequence_guard_install_wrapped", False):
        return
    original_install = guard_module.install

    def install(base) -> None:
        original_install(base)
        _install_state_wrapper(base)

    guard_module.install = install
    guard_module._price_sequence_guard_install_wrapped = True


__all__ = ["PATCH_VERSION", "install_runtime_wrapper"]
