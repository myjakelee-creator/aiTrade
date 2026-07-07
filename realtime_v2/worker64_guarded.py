from __future__ import annotations

from datetime import datetime
from typing import Any

from realtime_v2 import worker64 as base
from realtime_v2.common import (
    LARGE_TRADE_THRESHOLD_KRW,
    event_age_sec,
    normalize_code,
    normalize_trade_time,
    normalized_price,
    normalized_rate,
    normalized_trade_value_eok,
    now_text,
    to_int,
    to_number,
)

MAX_ACCEPT_LAG_SEC = 10.0


def _time_seconds(value: Any) -> int | None:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 6:
        return None
    digits = digits[:6]
    try:
        hour = int(digits[0:2])
        minute = int(digits[2:4])
        second = int(digits[4:6])
    except ValueError:
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def _now_seconds() -> int:
    now = datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second


def _lag_seconds(trade_time_seconds: int | None) -> float | None:
    if trade_time_seconds is None:
        return None
    lag = _now_seconds() - int(trade_time_seconds)
    if lag < -12 * 3600:
        lag += 24 * 3600
    return round(max(0.0, float(lag)), 3)


def _drop_trade(state, quote: dict[str, Any], code: str, reason: str, event: dict[str, Any], values: dict[str, Any], trade_time: str, lag_sec: float | None) -> None:
    state.status["dropped_trade_count"] = int(state.status.get("dropped_trade_count") or 0) + 1
    state.status["last_dropped_trade"] = {
        "stock_code": code,
        "reason": reason,
        "event_ts": event.get("ts"),
        "trade_time": trade_time,
        "fid20_lag_sec": lag_sec,
        "source_code": values.get("source_code") or values.get("registered_code"),
    }
    quote["last_dropped_trade_reason"] = reason
    quote["last_dropped_trade_at"] = now_text()
    if lag_sec is not None:
        quote["fid20_lag_sec"] = lag_sec
        quote["price_age_sec"] = max(to_number(quote.get("price_age_sec")) or 0, lag_sec)


def _guarded_apply_trade(self, event: dict[str, Any]) -> None:
    values = base.merged_event_values(event)
    raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
    code = normalize_code(
        event.get("stock_code")
        or event.get("received_code")
        or values.get("stock_code")
        or values.get("normalized_code")
        or values.get("received_code")
    )
    if not code:
        return
    quote = self._quote(code)
    price = normalized_price(
        raw.get("price_raw")
        or values.get("price")
        or values.get("trade_price")
        or values.get("realtime_price")
    )
    change_rate = normalized_rate(
        raw.get("change_rate_raw")
        or values.get("change_rate")
        or values.get("realtime_change_rate")
    )
    trade_qty = to_int(raw.get("trade_qty_raw") or values.get("trade_qty"))
    cumulative_volume = to_int(
        raw.get("cumulative_volume_raw") or values.get("cumulative_volume")
    )
    trade_value_eok = None
    if values.get("trade_value_eok") not in (None, ""):
        trade_value_eok = to_number(values.get("trade_value_eok"))
    if trade_value_eok is None:
        trade_value_eok = normalized_trade_value_eok(
            raw.get("cumulative_value_raw") or values.get("cumulative_value")
        )
    if trade_value_eok is not None:
        trade_value_eok = round(float(trade_value_eok), 4)
    strength = to_number(
        raw.get("execution_strength_raw") or values.get("execution_strength")
    )
    trade_time_raw = (
        raw.get("trade_time_raw")
        or values.get("fid20_trade_time")
        or values.get("trade_time")
    )
    trade_time = normalize_trade_time(trade_time_raw) or str(trade_time_raw or "")
    trade_time_sec = _time_seconds(trade_time_raw)
    fid20_lag_sec = _lag_seconds(trade_time_sec)
    received_at = (
        event.get("ts")
        or values.get("price_received_at")
        or values.get("trade_received_at")
        or values.get("received_at")
        or now_text()
    )

    previous_time_sec = quote.get("_trade_time_seconds")
    previous_value = to_number(quote.get("trade_value_eok"))
    previous_volume = to_int(quote.get("cumulative_volume"))
    drop_reason = None
    if trade_time_sec is not None and previous_time_sec is not None:
        try:
            if int(trade_time_sec) < int(previous_time_sec):
                drop_reason = "older_fid20_than_last_accepted"
        except (TypeError, ValueError):
            pass
    if drop_reason is None and trade_value_eok is not None and previous_value is not None:
        if float(trade_value_eok) + 1.0 < float(previous_value):
            drop_reason = "cumulative_trade_value_decreased"
    if drop_reason is None and cumulative_volume is not None and previous_volume is not None:
        if int(cumulative_volume) < int(previous_volume):
            drop_reason = "cumulative_volume_decreased"
    if drop_reason is None and fid20_lag_sec is not None and fid20_lag_sec > MAX_ACCEPT_LAG_SEC:
        # After halts/circuit breakers Kiwoom can deliver delayed backlog events.
        # They may have a fresh receive time but an old FID20 trade time. Do not
        # let those stale events overwrite the latest accepted quote.
        drop_reason = "fid20_lag_exceeds_guard"
    if drop_reason is not None:
        _drop_trade(self, quote, code, drop_reason, event, values, trade_time, fid20_lag_sec)
        return

    if price is not None:
        quote["price"] = price
        quote["trade_price"] = price
    if change_rate is not None:
        quote["change_rate"] = change_rate
    if trade_qty is not None:
        quote["trade_qty"] = trade_qty
    if cumulative_volume is not None:
        quote["cumulative_volume"] = cumulative_volume
    if trade_value_eok is not None:
        quote["trade_value_eok"] = trade_value_eok
    if strength is not None:
        quote["execution_strength"] = round(strength, 4)
    quote["trade_time"] = trade_time
    if trade_time_sec is not None:
        quote["_trade_time_seconds"] = trade_time_sec
    quote["fid20_lag_sec"] = fid20_lag_sec
    quote["received_at"] = received_at
    receive_age = event_age_sec(received_at) or 0
    quote["price_age_sec"] = max(receive_age, fid20_lag_sec or 0)
    quote["market_type_raw"] = raw.get("market_type_raw") or values.get("market_type")
    quote["source_code"] = values.get("source_code") or values.get("registered_code")
    quote.pop("last_dropped_trade_reason", None)
    self.status["trade_count"] += 1

    if trade_qty and price:
        trade_amount = abs(trade_qty) * price
        if trade_amount >= LARGE_TRADE_THRESHOLD_KRW:
            eok = trade_amount / 100_000_000
            if trade_qty > 0:
                quote["large_trade_buy_count"] += 1
                quote["large_trade_buy_sum_eok"] = round(
                    quote["large_trade_buy_sum_eok"] + eok, 4
                )
            elif trade_qty < 0:
                quote["large_trade_sell_count"] += 1
                quote["large_trade_sell_sum_eok"] = round(
                    quote["large_trade_sell_sum_eok"] + eok, 4
                )
            quote["large_trade_net_count"] = (
                quote["large_trade_buy_count"] - quote["large_trade_sell_count"]
            )
            quote["large_trade_net_sum_eok"] = round(
                quote["large_trade_buy_sum_eok"] - quote["large_trade_sell_sum_eok"],
                4,
            )
            daily_entry = self.daily_values_by_code.setdefault(code, {})
            for key in base.DAILY_PERSIST_KEYS:
                if key.startswith("large_trade_"):
                    daily_entry[key] = quote.get(key)
            self._mark_daily_dirty()


base.State._apply_trade = _guarded_apply_trade

if __name__ == "__main__":
    raise SystemExit(base.main())
