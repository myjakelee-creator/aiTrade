from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
from realtime_v2.market_session import market_session_now

STALE_LAG_WARN_SEC = 10.0


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


def _session_dict() -> dict[str, Any]:
    try:
        return market_session_now().to_dict()
    except Exception as error:
        return {
            "phase": "unknown",
            "phase_label": "장상태 알 수 없음",
            "trading_date": "",
            "calendar_date": "",
            "accept_realtime": True,
            "prefer_seed_when_no_realtime": True,
            "freeze_realtime_missing": True,
            "reason": str(error),
            "windows": {},
            "special_day": {},
            "is_trading_day": False,
        }


def _update_market_session_status(state) -> dict[str, Any]:
    session = _session_dict()
    state.status["market_phase"] = session.get("phase")
    state.status["market_phase_label"] = session.get("phase_label")
    state.status["market_trading_date"] = session.get("trading_date")
    state.status["market_accept_realtime"] = session.get("accept_realtime")
    state.status["market_freeze_realtime_missing"] = session.get("freeze_realtime_missing")
    previous_date = state.status.get("active_trading_date")
    current_date = session.get("trading_date")
    if current_date and previous_date and previous_date != current_date:
        state.status["date_rollover_detected"] = True
        state.status["date_rollover_from"] = previous_date
        state.status["date_rollover_to"] = current_date
        state.status["needs_universe_rebuild"] = True
    elif current_date and not previous_date:
        state.status["active_trading_date"] = current_date
    return session


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


def _mark_lag_warning(state, quote: dict[str, Any], code: str, values: dict[str, Any], trade_time: str, lag_sec: float | None) -> None:
    if lag_sec is None or lag_sec <= STALE_LAG_WARN_SEC:
        return
    state.status["lagged_trade_warning_count"] = int(state.status.get("lagged_trade_warning_count") or 0) + 1
    state.status["last_lagged_trade_warning"] = {
        "stock_code": code,
        "trade_time": trade_time,
        "fid20_lag_sec": lag_sec,
        "source_code": values.get("source_code") or values.get("registered_code"),
    }
    quote["fid20_lag_sec"] = lag_sec
    quote["last_lagged_trade_warning_at"] = now_text()


def _guarded_load_universe(self) -> None:
    """Load seed rows and create base quotes for the whole universe.

    During market halts, closing call auction, after-close gaps, or immediately
    after a v2 restart, many stocks may not receive a realtime trade event. If
    rows are created only from realtime trades, price/rate/value cells stay
    blank. The universe already contains the latest ka10032 seed snapshot; use
    it as the base row, then let realtime events overwrite it.
    """
    self.seed_by_code: dict[str, dict[str, Any]] = {}
    try:
        payload = json.loads(self.universe_file.read_text(encoding="utf-8-sig"))
        built_at = payload.get("built_at") if isinstance(payload, dict) else None
        for item in payload.get("items", []) or []:
            code = normalize_code(item.get("stock_code"))
            if not code:
                continue
            self.name_by_code[code] = str(item.get("stock_name") or code)
            self.seed_rank_by_code[code] = int(
                item.get("seed_rank") or item.get("original_rank") or 999999
            )
            previous_rank = to_int(item.get("prev_rank"))
            previous_value = to_number(item.get("prev_trade_value_eok"))
            if previous_rank is not None and previous_rank > 0:
                self.prev_rank_by_code[code] = previous_rank
            if previous_value is not None and previous_value > 0:
                self.prev_trade_value_by_code[code] = float(previous_value)
            self.seed_by_code[code] = {
                "seed_price": item.get("seed_price"),
                "seed_change_rate": item.get("seed_change_rate"),
                "seed_trade_value_eok": item.get("seed_trade_value_eok"),
                "seed_built_at": built_at,
            }
        _update_market_session_status(self)
        for code in list(self.seed_rank_by_code):
            self._quote(code)
        self.status["universe_seed_quote_count"] = len(self.quotes)
        self.status["universe_seed_built_at"] = built_at
    except Exception as error:
        self.status["last_error"] = f"universe load failed: {error}"


def _guarded_quote(self, code: str) -> dict[str, Any]:
    code = normalize_code(code)
    quote = self.quotes.get(code)
    if quote is not None:
        return quote
    persisted = self.daily_values_by_code.get(code) or {}
    seed = getattr(self, "seed_by_code", {}).get(code, {}) or {}
    quote = {
        "stock_code": code,
        "stock_name": self.name_by_code.get(code, code),
        "seed_rank": self.seed_rank_by_code.get(code, 999999),
        "prev_rank": self.prev_rank_by_code.get(code),
        "prev_trade_value_eok": self.prev_trade_value_by_code.get(code),
        "large_trade_buy_count": persisted.get("large_trade_buy_count", 0),
        "large_trade_sell_count": persisted.get("large_trade_sell_count", 0),
        "large_trade_net_count": persisted.get("large_trade_net_count", 0),
        "large_trade_buy_sum_eok": persisted.get("large_trade_buy_sum_eok", 0.0),
        "large_trade_sell_sum_eok": persisted.get("large_trade_sell_sum_eok", 0.0),
        "large_trade_net_sum_eok": persisted.get("large_trade_net_sum_eok", 0.0),
        "source_code": "seed_universe",
        "row_source": "seed_universe",
    }
    seed_price = normalized_price(seed.get("seed_price"))
    seed_rate = normalized_rate(seed.get("seed_change_rate"))
    seed_value = to_number(seed.get("seed_trade_value_eok"))
    if seed_price is not None:
        quote["price"] = seed_price
        quote["seed_price"] = seed_price
    if seed_rate is not None:
        quote["change_rate"] = seed_rate
        quote["seed_change_rate"] = seed_rate
    if seed_value is not None:
        quote["trade_value_eok"] = round(float(seed_value), 4)
        quote["seed_trade_value_eok"] = round(float(seed_value), 4)
    if seed.get("seed_built_at"):
        quote["seed_built_at"] = seed.get("seed_built_at")
    for key in ("program_net", "program_net_updated_at", "program_net_source", "program_net_status"):
        if key in persisted:
            quote[key] = persisted.get(key)
    self.quotes[code] = quote
    return quote


def _guarded_rows(self, limit: int = 300) -> list[dict[str, Any]]:
    with self.lock:
        _update_market_session_status(self)
        for code in list(self.seed_rank_by_code):
            self._quote(code)
        rows = [deepcopy(row) for row in self.quotes.values()]
    for row in rows:
        if row.get("received_at"):
            row["price_age_sec"] = event_age_sec(row.get("received_at"))
        elif row.get("price") is not None:
            row["price_age_sec"] = None
    rows.sort(
        key=lambda row: (
            -(to_number(row.get("trade_value_eok")) or 0),
            row.get("seed_rank") or 999999,
            row.get("stock_code") or "",
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        prev_rank = to_int(row.get("prev_rank"))
        row["rank_change"] = (prev_rank - rank) if prev_rank is not None else None
        previous_amount = to_number(row.get("prev_trade_value_eok"))
        current_amount = to_number(row.get("trade_value_eok"))
        if previous_amount is not None and previous_amount > 0 and current_amount is not None:
            row["amount_ratio"] = round(current_amount / previous_amount, 4)
        else:
            row["amount_ratio"] = None
    return rows[:limit]


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
    session = _update_market_session_status(self)
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
    has_accepted_realtime = quote.get("row_source") == "realtime"
    drop_reason = None
    if session.get("accept_realtime") is False and has_accepted_realtime:
        drop_reason = "market_session_closed"
    if drop_reason is None and has_accepted_realtime and trade_time_sec is not None and previous_time_sec is not None:
        try:
            if int(trade_time_sec) < int(previous_time_sec):
                drop_reason = "older_fid20_than_last_accepted"
        except (TypeError, ValueError):
            pass
    if drop_reason is None and has_accepted_realtime and trade_value_eok is not None and previous_value is not None:
        if float(trade_value_eok) + 1.0 < float(previous_value):
            drop_reason = "cumulative_trade_value_decreased"
    if drop_reason is None and has_accepted_realtime and cumulative_volume is not None and previous_volume is not None:
        if int(cumulative_volume) < int(previous_volume):
            drop_reason = "cumulative_volume_decreased"
    # Do NOT drop by absolute FID20 lag alone. During halts and closing phases,
    # Kiwoom can send delayed-but-monotonic events. Dropping every delayed first
    # event leaves price/rate/value blank. We warn on lag but accept monotonic
    # events; decreasing cumulative value/volume and older trade-time events are
    # still rejected after a realtime event has already been accepted.
    if drop_reason is not None:
        _drop_trade(self, quote, code, drop_reason, event, values, trade_time, fid20_lag_sec)
        return
    _mark_lag_warning(self, quote, code, values, trade_time, fid20_lag_sec)

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
    quote["price_age_sec"] = event_age_sec(received_at)
    quote["market_type_raw"] = raw.get("market_type_raw") or values.get("market_type")
    quote["source_code"] = values.get("source_code") or values.get("registered_code")
    quote["row_source"] = "realtime"
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


_original_snapshot = base.State.snapshot


def _guarded_snapshot(self, limit: int = 300) -> dict[str, Any]:
    session = _update_market_session_status(self)
    payload = _original_snapshot(self, limit)
    payload["market_session"] = session
    if isinstance(payload.get("status"), dict):
        payload["status"].update(
            {
                "market_phase": session.get("phase"),
                "market_phase_label": session.get("phase_label"),
                "market_trading_date": session.get("trading_date"),
                "market_accept_realtime": session.get("accept_realtime"),
                "market_freeze_realtime_missing": session.get("freeze_realtime_missing"),
                "market_session_reason": session.get("reason"),
            }
        )
    return payload


base.State._load_universe = _guarded_load_universe
base.State._quote = _guarded_quote
base.State.rows = _guarded_rows
base.State._apply_trade = _guarded_apply_trade
base.State.snapshot = _guarded_snapshot

if __name__ == "__main__":
    raise SystemExit(base.main())
