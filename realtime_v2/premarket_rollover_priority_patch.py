from __future__ import annotations

"""Let a verified new trading-day reset outrank the FID20 midnight wrap.

The previous exact-close quote can hold FID20 near 20:00 while the next premarket
starts near 08:00. The numeric time is therefore smaller even though the event is
from a verified newer trading date. This wrapper leaves the existing v4 guard in
charge of all field filtering and only prevents that verified day rollover from being
misclassified as same-day SOR interleaving.

The first current-day event may still be applied to a `portable_exact_close` HOLD row,
so both realtime and verified hold rows are eligible for this one rollover check.
The same install hook also attaches the output-only current-day ranking policy so
previous-day HOLD amounts cannot compete with current-day cumulative trade value.

No collector, QAx, FID, request, thread, timer, browser, or SSE cadence changes.
"""

from typing import Any

PATCH_VERSION = "trade_field_regression_guard_v5"
_ELIGIBLE_ROW_SOURCES = {"realtime", "portable_exact_close"}


def _install_state_wrapper(base, guard_module) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_premarket_rollover_priority_installed", False
    ):
        return

    guarded_apply = state_class._apply_trade

    def apply_trade(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return guarded_apply(self, event)

        values, raw = guard_module._event_values(base, event)
        code = guard_module.normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return guarded_apply(self, event)

        incoming_value = (
            guard_module.to_number(values.get("trade_value_eok"))
            if values.get("trade_value_eok") not in (None, "")
            else None
        )
        if incoming_value is None:
            incoming_value = guard_module.normalized_trade_value_eok(
                raw.get("cumulative_value_raw") or values.get("cumulative_value")
            )
        incoming_volume = guard_module.to_int(
            raw.get("cumulative_volume_raw") or values.get("cumulative_volume")
        )
        if incoming_value is None and incoming_volume is None:
            return guarded_apply(self, event)

        current_date = guard_module._verified_current_date(self, event, values)
        incoming_time_raw = (
            raw.get("trade_time_raw")
            or values.get("fid20_trade_time")
            or values.get("trade_time")
        )

        import realtime_v2.worker64_guarded as guarded

        incoming_time = guarded._time_seconds(incoming_time_raw)
        temporary_time_applied = False
        previous_time = None
        before_trade_count = 0

        with self.lock:
            quote = self.quotes.get(code)
            if not isinstance(quote, dict) or str(quote.get("row_source") or "") not in _ELIGIBLE_ROW_SOURCES:
                return guarded_apply(self, event)

            previous_value = guard_module.to_number(quote.get("trade_value_eok"))
            previous_volume = guard_module.to_int(quote.get("cumulative_volume"))
            previous_value_date = guard_module._field_date(
                quote, "trade_value_trading_date", "source_trading_date"
            )
            previous_volume_date = guard_module._field_date(
                quote,
                "cumulative_volume_trading_date",
                "trade_value_trading_date",
                "source_trading_date",
            )
            value_rollover = bool(
                incoming_value is not None
                and previous_value is not None
                and float(incoming_value) + 1.0 < float(previous_value)
                and guard_module._is_verified_next_day(previous_value_date, current_date)
            )
            volume_rollover = bool(
                incoming_volume is not None
                and previous_volume is not None
                and int(incoming_volume) < int(previous_volume)
                and guard_module._is_verified_next_day(previous_volume_date, current_date)
            )
            previous_time = quote.get("_trade_time_seconds")
            older_time = bool(
                incoming_time is not None
                and previous_time is not None
                and int(incoming_time) < int(previous_time)
            )
            if older_time and (value_rollover or volume_rollover):
                before_trade_count = int(self.status.get("trade_count") or 0)
                quote["_trade_time_seconds"] = incoming_time
                temporary_time_applied = True

        try:
            result = guarded_apply(self, event)
        except Exception:
            if temporary_time_applied:
                with self.lock:
                    quote = self.quotes.get(code)
                    if isinstance(quote, dict):
                        quote["_trade_time_seconds"] = previous_time
            raise

        with self.lock:
            quote = self.quotes.get(code)
            after_trade_count = int(self.status.get("trade_count") or 0)
            if temporary_time_applied and isinstance(quote, dict):
                if after_trade_count <= before_trade_count:
                    quote["_trade_time_seconds"] = previous_time
                else:
                    self.status["premarket_rollover_time_wrap_accepted_count"] = int(
                        self.status.get("premarket_rollover_time_wrap_accepted_count") or 0
                    ) + 1
                    self.status["last_premarket_rollover_time_wrap_accepted"] = {
                        "stock_code": code,
                        "from_trade_time_seconds": previous_time,
                        "to_trade_time_seconds": incoming_time,
                        "to_trading_date": current_date,
                    }
                    self.status["trade_field_regression_guard_version"] = PATCH_VERSION
        return result

    state_class._apply_trade = apply_trade
    state_class._stockboard_premarket_rollover_priority_installed = True


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_trade_field_regression_guard as guard_module

    if getattr(guard_module, "_premarket_rollover_install_wrapped", False):
        return
    original_install = guard_module.install

    def install(base) -> None:
        original_install(base)
        _install_state_wrapper(base, guard_module)
        from realtime_v2.current_day_trade_value_rank_patch import install as install_rank

        install_rank(base)

    guard_module.install = install
    guard_module._premarket_rollover_install_wrapped = True
