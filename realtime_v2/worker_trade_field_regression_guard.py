from __future__ import annotations

"""Protect latest price/rate without accepting regressed cumulative fields.

The production QAx collector samples FID14 while price/FID20 arrive on every trade.
For SOR (`_AL`) source streams, FID20 and cumulative values can interleave across
venues. The previous guard rejected the whole event, which discarded the newest
price and change-rate observed by the QAx callback. This patch keeps arrival-order
price/rate while holding monotonic time and cumulative fields at their last good
values.

No collector, FID, thread, timer, request, or browser cadence is changed.
"""

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, normalized_trade_value_eok, to_int, to_number

PATCH_VERSION = "trade_field_regression_guard_v2"


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

        with self.lock:
            quote = self.quotes.get(code)
            quote = dict(quote) if isinstance(quote, dict) else {}

        if quote.get("row_source") != "realtime":
            return original_apply(self, event)

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

        incoming_value = (
            to_number(values.get("trade_value_eok"))
            if values.get("trade_value_eok") not in (None, "")
            else None
        )
        if incoming_value is None:
            incoming_value = normalized_trade_value_eok(
                raw.get("cumulative_value_raw") or values.get("cumulative_value")
            )
        previous_value = to_number(quote.get("trade_value_eok"))
        value_regressed = (
            incoming_value is not None
            and previous_value is not None
            and float(incoming_value) + 1.0 < float(previous_value)
        )

        incoming_volume = to_int(
            raw.get("cumulative_volume_raw") or values.get("cumulative_volume")
        )
        previous_volume = to_int(quote.get("cumulative_volume"))
        volume_regressed = (
            incoming_volume is not None
            and previous_volume is not None
            and int(incoming_volume) < int(previous_volume)
        )

        if not sor_interleaved_time and not value_regressed and not volume_regressed:
            return original_apply(self, event)

        replacement_time = None
        if sor_interleaved_time and previous_time is not None:
            replacement_time = str(quote.get("trade_time") or f"{int(previous_time):06d}")

        filtered = _filtered_event(
            event,
            trade_value=value_regressed or sor_interleaved_time,
            volume=volume_regressed or sor_interleaved_time,
            replacement_trade_time=replacement_time,
        )
        with self.lock:
            self.status["trade_field_regression_guard_version"] = PATCH_VERSION
            self.status["trade_field_regression_suppressed_count"] = int(
                self.status.get("trade_field_regression_suppressed_count") or 0
            ) + 1
            if sor_interleaved_time:
                _increment_reason(
                    self.status,
                    "trade_field_regression_suppressed_reason_counts",
                    "sor_fid20_interleaved_price_preserved",
                )
            if value_regressed:
                _increment_reason(
                    self.status,
                    "trade_field_regression_suppressed_reason_counts",
                    "cumulative_trade_value_decreased",
                )
            if volume_regressed:
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
            }

        return original_apply(self, filtered)

    guarded._drop_trade = drop_trade
    state_class._apply_trade = apply_trade
    state_class._stockboard_trade_field_regression_guard_installed = True
    state_class._stockboard_trade_field_regression_guard_version = PATCH_VERSION
