from __future__ import annotations

"""Protect latest price/rate without rejecting a verified daily cumulative reset.

The production QAx collector samples FID14 while price/FID20 arrive on every trade.
For SOR (`_AL`) source streams, FID20 and cumulative values can interleave across
venues. Same-day regressions therefore keep the previous monotonic cumulative fields
while allowing arrival-order price/rate.

At the next verified trading day, however, cumulative trade value and volume naturally
restart from a smaller value. This patch accepts that reset only when all of the
following agree:

- the Worker has an active current trading date,
- the collector event receive date matches that trading date, and
- the existing cumulative field is explicitly tagged with an older trading date.

No collector, FID, thread, timer, request, browser calculation, or SSE cadence changes.
"""

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, normalized_trade_value_eok, to_int, to_number

PATCH_VERSION = "trade_field_regression_guard_v3"


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _event_values(base, event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    values = base.merged_event_values(event)
    raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
    return values if isinstance(values, dict) else {}, raw if isinstance(raw, dict) else {}


def _event_containers(event: dict[str, Any]) -> list[dict[str, Any]]:
    containers: list[dict[str, Any]] = []
    for key in ("kwargs", "values"):
        value = event.get(key)
        if isinstance(value, dict):
            containers.append(value)
            raw = value.get("raw")
            if isinstance(raw, dict):
                containers.append(raw)
    return containers


def _filtered_event(
    event: dict[str, Any],
    *,
    trade_value: bool,
    volume: bool,
    replacement_trade_time: str | None = None,
) -> dict[str, Any]:
    result = deepcopy(event)
    for container in _event_containers(result):
        if trade_value:
            for key in ("trade_value_eok", "cumulative_value", "cumulative_value_raw"):
                container.pop(key, None)
        if volume:
            for key in ("cumulative_volume", "cumulative_volume_raw"):
                container.pop(key, None)
        if replacement_trade_time:
            for key in ("trade_time", "fid20_trade_time", "trade_time_raw"):
                if key in container or key == "trade_time":
                    container[key] = replacement_trade_time
    return result


def _increment_reason(status: dict[str, Any], key: str, reason: str) -> None:
    counts = status.get(key)
    counts = dict(counts) if isinstance(counts, dict) else {}
    counts[reason] = int(counts.get(reason) or 0) + 1
    status[key] = counts


def _source_code(event: dict[str, Any], values: dict[str, Any]) -> str:
    return str(
        values.get("source_code")
        or values.get("registered_code")
        or event.get("received_code")
        or ""
    ).strip()


def _status_trading_dates(state) -> set[str]:
    status = getattr(state, "status", {})
    result: set[str] = set()
    if isinstance(status, dict):
        for key in (
            "board_display_current_trading_date",
            "market_trading_date",
            "board_expected_trading_date",
        ):
            date_text = _date_digits(status.get(key))
            if date_text:
                result.add(date_text)
    return result


def _event_receive_date(event: dict[str, Any], values: dict[str, Any]) -> str:
    for key in (
        "trading_date",
        "market_trading_date",
        "price_trading_date",
        "trade_value_trading_date",
    ):
        date_text = _date_digits(values.get(key) or event.get(key))
        if date_text:
            return date_text

    for value in (
        event.get("ts"),
        values.get("price_received_at"),
        values.get("trade_received_at"),
        values.get("received_at"),
    ):
        date_text = _date_digits(value)
        if date_text:
            return date_text
    return ""


def _verified_current_date(state, event: dict[str, Any], values: dict[str, Any]) -> str:
    event_date = _event_receive_date(event, values)
    if not event_date:
        return ""

    if event_date in _status_trading_dates(state):
        return event_date

    try:
        from realtime_v2.worker_board_trading_date_guard import board_target_context

        target_date, _phase, active = board_target_context()
        target_date = _date_digits(target_date)
        return event_date if active and target_date == event_date else ""
    except Exception:
        return ""


def _field_date(quote: dict[str, Any], *keys: str) -> str:
    for key in keys:
        date_text = _date_digits(quote.get(key))
        if date_text:
            return date_text
    return ""


def _is_verified_next_day(previous_date: str, current_date: str) -> bool:
    return bool(
        len(previous_date) == 8
        and len(current_date) == 8
        and previous_date < current_date
    )


def _stamp_accepted_cumulative_dates(
    quote: dict[str, Any],
    *,
    current_date: str,
    incoming_value: float | None,
    incoming_volume: int | None,
    value_was_suppressed: bool,
    volume_was_suppressed: bool,
) -> None:
    if not current_date:
        return
    if incoming_value is not None and not value_was_suppressed:
        accepted_value = to_number(quote.get("trade_value_eok"))
        if accepted_value is not None and abs(float(accepted_value) - float(incoming_value)) <= 0.0001:
            quote["trade_value_trading_date"] = current_date
    if incoming_volume is not None and not volume_was_suppressed:
        accepted_volume = to_int(quote.get("cumulative_volume"))
        if accepted_volume is not None and int(accepted_volume) == int(incoming_volume):
            quote["cumulative_volume_trading_date"] = current_date


def install(base) -> None:
    import realtime_v2.worker64_guarded as guarded

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_trade_field_regression_guard_installed", False
    ):
        return

    original_apply = state_class._apply_trade
    original_drop = guarded._drop_trade

    def drop_trade(state, quote, code, reason, event, values, trade_time, lag_sec):
        with state.lock:
            _increment_reason(state.status, "dropped_trade_reason_counts", str(reason or "unknown"))
            state.status["trade_field_regression_guard_version"] = PATCH_VERSION
        return original_drop(
            state,
            quote,
            code,
            reason,
            event,
            values,
            trade_time,
            lag_sec,
        )

    def apply_trade(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return original_apply(self, event)

        values, raw = _event_values(base, event)
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return original_apply(self, event)

        incoming_value = (
            to_number(values.get("trade_value_eok"))
            if values.get("trade_value_eok") not in (None, "")
            else None
        )
        if incoming_value is None:
            incoming_value = normalized_trade_value_eok(
                raw.get("cumulative_value_raw") or values.get("cumulative_value")
            )
        incoming_volume = to_int(
            raw.get("cumulative_volume_raw") or values.get("cumulative_volume")
        )
        has_cumulative_input = incoming_value is not None or incoming_volume is not None
        current_date = (
            _verified_current_date(self, event, values) if has_cumulative_input else ""
        )

        with self.lock:
            quote = self.quotes.get(code)
            quote = quote if isinstance(quote, dict) else None
            before_trade_count = int(self.status.get("trade_count") or 0)

        if quote is None or quote.get("row_source") != "realtime":
            result = original_apply(self, event)
            if has_cumulative_input:
                with self.lock:
                    accepted_quote = self.quotes.get(code)
                    after_trade_count = int(self.status.get("trade_count") or 0)
                    if isinstance(accepted_quote, dict) and after_trade_count > before_trade_count:
                        _stamp_accepted_cumulative_dates(
                            accepted_quote,
                            current_date=current_date,
                            incoming_value=incoming_value,
                            incoming_volume=incoming_volume,
                            value_was_suppressed=False,
                            volume_was_suppressed=False,
                        )
                        self.status["trade_field_regression_guard_version"] = PATCH_VERSION
            return result

        incoming_time_raw = (
            raw.get("trade_time_raw")
            or values.get("fid20_trade_time")
            or values.get("trade_time")
        )
        incoming_time = guarded._time_seconds(incoming_time_raw)
        previous_time = quote.get("_trade_time_seconds")
        try:
            older_time = (
                incoming_time is not None
                and previous_time is not None
                and int(incoming_time) < int(previous_time)
            )
        except (TypeError, ValueError):
            older_time = False

        source_code = _source_code(event, values)
        sor_interleaved_time = older_time and source_code.upper().endswith("_AL")
        if older_time and not sor_interleaved_time:
            return original_apply(self, event)

        previous_value = to_number(quote.get("trade_value_eok"))
        value_regressed = (
            incoming_value is not None
            and previous_value is not None
            and float(incoming_value) + 1.0 < float(previous_value)
        )

        previous_volume = to_int(quote.get("cumulative_volume"))
        volume_regressed = (
            incoming_volume is not None
            and previous_volume is not None
            and int(incoming_volume) < int(previous_volume)
        )

        previous_value_date = _field_date(
            quote, "trade_value_trading_date", "source_trading_date"
        )
        previous_volume_date = _field_date(
            quote,
            "cumulative_volume_trading_date",
            "trade_value_trading_date",
            "source_trading_date",
        )
        daily_value_reset = bool(
            value_regressed
            and not sor_interleaved_time
            and _is_verified_next_day(previous_value_date, current_date)
        )
        daily_volume_reset = bool(
            volume_regressed
            and not sor_interleaved_time
            and _is_verified_next_day(previous_volume_date, current_date)
        )

        suppress_value = bool(
            sor_interleaved_time or (value_regressed and not daily_value_reset)
        )
        suppress_volume = bool(
            sor_interleaved_time or (volume_regressed and not daily_volume_reset)
        )

        if not sor_interleaved_time and not value_regressed and not volume_regressed:
            result = original_apply(self, event)
            if has_cumulative_input:
                with self.lock:
                    accepted_quote = self.quotes.get(code)
                    after_trade_count = int(self.status.get("trade_count") or 0)
                    if isinstance(accepted_quote, dict) and after_trade_count > before_trade_count:
                        _stamp_accepted_cumulative_dates(
                            accepted_quote,
                            current_date=current_date,
                            incoming_value=incoming_value,
                            incoming_volume=incoming_volume,
                            value_was_suppressed=False,
                            volume_was_suppressed=False,
                        )
                        self.status["trade_field_regression_guard_version"] = PATCH_VERSION
            return result

        replacement_time = None
        if sor_interleaved_time and previous_time is not None:
            replacement_time = str(quote.get("trade_time") or f"{int(previous_time):06d}")

        filtered = _filtered_event(
            event,
            trade_value=suppress_value,
            volume=suppress_volume,
            replacement_trade_time=replacement_time,
        )

        saved_reset_fields: dict[str, Any] = {}
        reset_keys: list[str] = []
        if daily_value_reset:
            reset_keys.extend(
                (
                    "trade_value_eok",
                    "trade_value_trading_date",
                    "amount_ratio",
                    "amount_ratio_missing_reason",
                )
            )
        if daily_volume_reset:
            reset_keys.extend(("cumulative_volume", "cumulative_volume_trading_date"))

        with self.lock:
            live_quote = self.quotes.get(code)
            if isinstance(live_quote, dict):
                for key in dict.fromkeys(reset_keys):
                    if key in live_quote:
                        saved_reset_fields[key] = deepcopy(live_quote.get(key))
                        live_quote.pop(key, None)

            self.status["trade_field_regression_guard_version"] = PATCH_VERSION
            if sor_interleaved_time or suppress_value or suppress_volume:
                self.status["trade_field_regression_suppressed_count"] = int(
                    self.status.get("trade_field_regression_suppressed_count") or 0
                ) + 1
                if sor_interleaved_time:
                    _increment_reason(
                        self.status,
                        "trade_field_regression_suppressed_reason_counts",
                        "sor_fid20_interleaved_price_preserved",
                    )
                if suppress_value and value_regressed:
                    _increment_reason(
                        self.status,
                        "trade_field_regression_suppressed_reason_counts",
                        "cumulative_trade_value_decreased",
                    )
                if suppress_volume and volume_regressed:
                    _increment_reason(
                        self.status,
                        "trade_field_regression_suppressed_reason_counts",
                        "cumulative_volume_decreased",
                    )
                self.status["last_trade_field_regression_suppressed"] = {
                    "stock_code": code,
                    "trade_time": str(incoming_time_raw or ""),
                    "held_trade_time": replacement_time,
                    "source_code": source_code,
                    "incoming_trade_value_eok": incoming_value,
                    "previous_trade_value_eok": previous_value,
                    "incoming_cumulative_volume": incoming_volume,
                    "previous_cumulative_volume": previous_volume,
                    "previous_trade_value_date": previous_value_date or None,
                    "previous_cumulative_volume_date": previous_volume_date or None,
                    "current_trading_date": current_date or None,
                }

        try:
            result = original_apply(self, filtered)
        except Exception:
            with self.lock:
                restore_quote = self.quotes.get(code)
                if isinstance(restore_quote, dict):
                    for key in dict.fromkeys(reset_keys):
                        restore_quote.pop(key, None)
                    restore_quote.update(saved_reset_fields)
            raise

        with self.lock:
            accepted_quote = self.quotes.get(code)
            after_trade_count = int(self.status.get("trade_count") or 0)
            accepted = isinstance(accepted_quote, dict) and after_trade_count > before_trade_count
            if not accepted and isinstance(accepted_quote, dict):
                for key in dict.fromkeys(reset_keys):
                    accepted_quote.pop(key, None)
                accepted_quote.update(saved_reset_fields)
            elif isinstance(accepted_quote, dict):
                _stamp_accepted_cumulative_dates(
                    accepted_quote,
                    current_date=current_date,
                    incoming_value=incoming_value,
                    incoming_volume=incoming_volume,
                    value_was_suppressed=suppress_value,
                    volume_was_suppressed=suppress_volume,
                )
                if daily_value_reset or daily_volume_reset:
                    self.status["daily_cumulative_reset_accepted_count"] = int(
                        self.status.get("daily_cumulative_reset_accepted_count") or 0
                    ) + 1
                    _increment_reason(
                        self.status,
                        "trade_field_regression_accepted_reason_counts",
                        "daily_cumulative_reset_accepted",
                    )
                    self.status["last_daily_cumulative_reset_accepted"] = {
                        "stock_code": code,
                        "source_code": source_code,
                        "from_trade_value_date": previous_value_date or None,
                        "from_cumulative_volume_date": previous_volume_date or None,
                        "to_trading_date": current_date,
                        "incoming_trade_value_eok": incoming_value,
                        "previous_trade_value_eok": previous_value,
                        "incoming_cumulative_volume": incoming_volume,
                        "previous_cumulative_volume": previous_volume,
                        "trade_value_reset": daily_value_reset,
                        "cumulative_volume_reset": daily_volume_reset,
                    }
        return result

    guarded._drop_trade = drop_trade
    state_class._apply_trade = apply_trade
    state_class._stockboard_trade_field_regression_guard_installed = True
    state_class._stockboard_trade_field_regression_guard_version = PATCH_VERSION
