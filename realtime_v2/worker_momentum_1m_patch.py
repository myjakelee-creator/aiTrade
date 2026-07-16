from __future__ import annotations

import math
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "momentum_1m_v1"
MOMENTUM_SOURCE = "kiwoom_ws_0B_1m"
BADGE_HOLD_MINUTES = 3
VWAP_SCALE_CANDIDATES = (1.0, 10.0, 100.0, 1_000.0, 10_000.0, 100_000.0, 1_000_000.0, 10_000_000.0)
VWAP_PRICE_RATIO_MIN = 0.5
VWAP_PRICE_RATIO_MAX = 1.5
SIGNAL_PHASES = {"opening_burst", "regular", "closing_call", "after_wait", "aftermarket"}
CLOSE_HOLD_PHASES = {"closed", "before_market", "weekend", "holiday", "outside"}

PERSIST_KEYS = (
    "momentum_vwap_signal",
    "momentum_vwap_signal_minute",
    "momentum_vwap_signal_at",
    "momentum_vwap_detail",
    "momentum_open_signal",
    "momentum_open_signal_minute",
    "momentum_open_signal_at",
    "momentum_open_detail",
    "momentum_last_completed_candle",
    "momentum_trading_date",
    "momentum_source",
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    try:
        result = float(number)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _clock_minutes(value: Any, default: int = 540) -> int:
    text = str(value or "").strip()
    try:
        hour, minute = text.split(":", 1)
        return int(hour) * 60 + int(minute[:2])
    except (TypeError, ValueError):
        return default


def _session(now: datetime | None = None):
    return market_session_now(now or datetime.now())


def _expected_date(now: datetime | None = None) -> str:
    current = now or datetime.now()
    session = _session(current)
    try:
        from realtime_v2 import worker_six_metric_lifecycle_patch as lifecycle

        expected = lifecycle._expected_date(session, current)
    except Exception:
        expected = getattr(session, "trading_date", "") or getattr(session, "calendar_date", "")
    return _date_digits(expected)


def _source_minute(event: dict[str, Any], trading_date: str) -> tuple[int, int, str] | None:
    text = "".join(character for character in str(event.get("execution_strength_source_time") or "") if character.isdigit())
    if len(text) < 6 or not trading_date:
        return None
    try:
        hour = int(text[:2])
        minute = int(text[2:4])
        second = int(text[4:6])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    day_key = int(trading_date)
    minute_key = day_key * 1440 + hour * 60 + minute
    second_of_day = hour * 3600 + minute * 60 + second
    return minute_key, second_of_day, f"{hour:02d}:{minute:02d}"


def _system_minute_key(now: datetime | None = None) -> int | None:
    current = now or datetime.now()
    trading_date = _expected_date(current)
    if not trading_date:
        return None
    return int(trading_date) * 1440 + current.hour * 60 + current.minute


def _nested_open(source: dict[str, Any]) -> float | None:
    for key in ("day_open", "open", "open_price", "day_open_price"):
        value = _number(source.get(key))
        if value is not None and value > 0:
            return abs(value)
    for key in ("display_ohlc", "realtime_ohlc", "ohlc"):
        nested = source.get(key)
        if isinstance(nested, dict):
            value = _number(nested.get("open"))
            if value is not None and value > 0:
                return abs(value)
    return None


def infer_vwap(
    cumulative_value_raw: Any,
    cumulative_volume: Any,
    trade_price: Any,
    preferred_scale: float | None = None,
) -> tuple[float | None, float | None, str]:
    raw_value = _number(cumulative_value_raw)
    volume = _number(cumulative_volume)
    price = _number(trade_price)
    if raw_value is None or volume is None or price is None:
        return None, preferred_scale, "missing_cumulative_input"
    raw_value, volume, price = abs(raw_value), abs(volume), abs(price)
    if raw_value <= 0 or volume <= 0 or price <= 0:
        return None, preferred_scale, "nonpositive_cumulative_input"
    raw_per_share = raw_value / volume

    def candidate(scale: float) -> tuple[float, float]:
        value = raw_per_share * scale
        return value, value / price

    if preferred_scale is not None and preferred_scale > 0:
        value, ratio = candidate(preferred_scale)
        if VWAP_PRICE_RATIO_MIN <= ratio <= VWAP_PRICE_RATIO_MAX:
            return round(value, 4), float(preferred_scale), "validated_existing_scale"

    best: tuple[float, float, float] | None = None
    for scale in VWAP_SCALE_CANDIDATES:
        value, ratio = candidate(scale)
        if value <= 0 or ratio <= 0:
            continue
        score = abs(math.log(ratio))
        if best is None or score < best[0]:
            best = (score, scale, value)
    if best is None:
        return None, preferred_scale, "scale_unresolved"
    _score, scale, value = best
    ratio = value / price
    if not (VWAP_PRICE_RATIO_MIN <= ratio <= VWAP_PRICE_RATIO_MAX):
        return None, preferred_scale, "scale_out_of_range"
    return round(value, 4), float(scale), "validated_inferred_scale"


def detect_vwap_signal(current: dict[str, Any], previous: dict[str, Any] | None) -> str | None:
    close = _number(current.get("close"))
    open_price = _number(current.get("open"))
    high = _number(current.get("high"))
    low = _number(current.get("low"))
    vwap = _number(current.get("vwap"))
    if None in (close, open_price, high, low, vwap):
        return None
    consecutive = bool(previous) and int(current.get("minute_key") or 0) == int(previous.get("minute_key") or -2) + 1
    if consecutive:
        previous_close = _number(previous.get("close"))
        previous_vwap = _number(previous.get("vwap"))
        if previous_close is not None and previous_vwap is not None:
            if close > vwap and previous_close < previous_vwap:
                return "가중돌파"
            if close < vwap and previous_close > previous_vwap:
                return "가중붕괴"
    if close > vwap and open_price >= vwap and low <= vwap:
        return "가중지지"
    if close < vwap and open_price <= vwap and high >= vwap:
        return "가중저항"
    return None


def detect_open_signal(current: dict[str, Any], previous: dict[str, Any] | None, day_open: Any) -> str | None:
    reference = _number(day_open)
    close = _number(current.get("close"))
    open_price = _number(current.get("open"))
    high = _number(current.get("high"))
    low = _number(current.get("low"))
    if reference is None or None in (close, open_price, high, low):
        return None
    consecutive = bool(previous) and int(current.get("minute_key") or 0) == int(previous.get("minute_key") or -2) + 1
    if consecutive:
        previous_close = _number(previous.get("close"))
        if previous_close is not None:
            if close > reference and previous_close < reference:
                return "시가돌파"
            if close < reference and previous_close > reference:
                return "시가붕괴"
    if close > reference and open_price >= reference and low <= reference:
        return "시가지지"
    if close < reference and open_price <= reference and high >= reference:
        return "시가저항"
    return None


def _new_candle(
    *,
    minute_key: int,
    second_of_day: int,
    minute_text: str,
    trading_date: str,
    price: float,
    vwap: float | None,
    vwap_quality: str,
    phase: str,
    partial: bool,
    cumulative_volume: Any,
) -> dict[str, Any]:
    return {
        "minute_key": minute_key,
        "minute_text": minute_text,
        "trading_date": trading_date,
        "phase": phase,
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "first_second": second_of_day,
        "last_second": second_of_day,
        "last_cumulative_volume": _number(cumulative_volume),
        "vwap": vwap,
        "vwap_quality": vwap_quality,
        "partial": bool(partial),
    }


def _update_candle(candle: dict[str, Any], event: dict[str, Any], second_of_day: int, price: float, vwap: float | None, vwap_quality: str) -> None:
    candle["high"] = max(float(candle.get("high") or price), price)
    candle["low"] = min(float(candle.get("low") or price), price)
    if second_of_day < int(candle.get("first_second") or second_of_day):
        candle["first_second"] = second_of_day
        candle["open"] = price
    cumulative_volume = _number(event.get("cumulative_volume"))
    last_second = int(candle.get("last_second") or -1)
    last_volume = _number(candle.get("last_cumulative_volume"))
    is_latest = second_of_day > last_second or (
        second_of_day == last_second
        and cumulative_volume is not None
        and (last_volume is None or cumulative_volume >= last_volume)
    )
    if is_latest:
        candle["last_second"] = second_of_day
        candle["last_cumulative_volume"] = cumulative_volume
        candle["close"] = price
        if vwap is not None:
            candle["vwap"] = vwap
            candle["vwap_quality"] = vwap_quality


def _detail(candle: dict[str, Any], previous: dict[str, Any] | None, day_open: float | None) -> dict[str, Any]:
    return {
        "minute": candle.get("minute_text"),
        "open": candle.get("open"),
        "high": candle.get("high"),
        "low": candle.get("low"),
        "close": candle.get("close"),
        "vwap": candle.get("vwap"),
        "previous_close": previous.get("close") if isinstance(previous, dict) else None,
        "previous_vwap": previous.get("vwap") if isinstance(previous, dict) else None,
        "day_open": day_open,
        "vwap_quality": candle.get("vwap_quality"),
    }


def install(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(state_class, "_stockboard_momentum_1m_installed", False):
        return
    original_init = state_class.__init__
    original_stage = getattr(state_class, "stage_approved_trade_events", None)
    original_rows = state_class.rows
    original_reset = getattr(state_class, "reset_approved_minute_pipeline_for_date", None)
    if not callable(original_stage):
        return

    base.DAILY_PERSIST_KEYS = tuple(dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *PERSIST_KEYS)))

    def state_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._momentum_current: dict[str, dict[str, Any]] = {}
        self._momentum_last_completed: dict[str, dict[str, Any]] = {}
        self._momentum_vwap_scale: dict[str, float] = {}
        self._momentum_started_minute = _system_minute_key()
        expected = _expected_date()
        with self.lock:
            for code, daily in self.daily_values_by_code.items():
                if not isinstance(daily, dict) or _date_digits(daily.get("momentum_trading_date")) != expected:
                    continue
                candle = daily.get("momentum_last_completed_candle")
                if isinstance(candle, dict) and _date_digits(candle.get("trading_date")) == expected:
                    self._momentum_last_completed[normalize_code(code)] = deepcopy(candle)
            self.status.update(
                {
                    "momentum_1m_installed": True,
                    "momentum_1m_version": PATCH_VERSION,
                    "momentum_1m_source": MOMENTUM_SOURCE,
                    "momentum_1m_badge_hold_minutes": BADGE_HOLD_MINUTES,
                    "momentum_1m_extra_qax_fids": 0,
                    "momentum_1m_extra_rest_requests": 0,
                    "momentum_1m_extra_websockets": 0,
                }
            )

    def store_signal(self, code: str, reference: str, signal: str, candle: dict[str, Any], previous: dict[str, Any] | None, day_open: float | None) -> None:
        signal_at = now_text()
        values = {
            f"momentum_{reference}_signal": signal,
            f"momentum_{reference}_signal_minute": candle.get("minute_key"),
            f"momentum_{reference}_signal_at": signal_at,
            f"momentum_{reference}_detail": _detail(candle, previous, day_open),
            "momentum_trading_date": candle.get("trading_date"),
            "momentum_source": MOMENTUM_SOURCE,
        }
        quote = self._quote(code)
        daily = self.daily_values_by_code.setdefault(code, {})
        for target in (quote, daily):
            target.update(deepcopy(values))

    def finalize_candle(self, code: str, candle: dict[str, Any]) -> bool:
        if not isinstance(candle, dict):
            return False
        previous = self._momentum_last_completed.get(code)
        full = not bool(candle.get("partial"))
        phase = str(candle.get("phase") or "")
        source = {
            **dict(self.daily_values_by_code.get(code) or {}),
            **dict(self.quotes.get(code) or {}),
        }
        day_open = _nested_open(source)
        changed = False
        if full and phase in SIGNAL_PHASES:
            vwap_signal = detect_vwap_signal(candle, previous)
            open_signal = detect_open_signal(candle, previous, day_open)
            if vwap_signal:
                store_signal(self, code, "vwap", vwap_signal, candle, previous, day_open)
                changed = True
            if open_signal:
                store_signal(self, code, "open", open_signal, candle, previous, day_open)
                changed = True
        if full:
            self._momentum_last_completed[code] = deepcopy(candle)
            values = {
                "momentum_last_completed_candle": deepcopy(candle),
                "momentum_trading_date": candle.get("trading_date"),
                "momentum_source": MOMENTUM_SOURCE,
            }
            quote = self._quote(code)
            daily = self.daily_values_by_code.setdefault(code, {})
            for target in (quote, daily):
                target.update(deepcopy(values))
            changed = True
        return changed

    def process_events(self, events: list[dict[str, Any]]) -> bool:
        changed = False
        session = _session()
        trading_date = _date_digits(getattr(session, "trading_date", "") or getattr(session, "calendar_date", ""))
        phase = str(getattr(session, "phase", "") or "")
        windows = getattr(session, "windows", {}) or {}
        regular_start = _clock_minutes(windows.get("regular_start"), 540)
        with self.lock:
            for event in events:
                if not isinstance(event, dict):
                    continue
                code = normalize_code(event.get("stock_code"))
                price = _number(event.get("execution_strength_trade_price"))
                source_minute = _source_minute(event, trading_date)
                if not code or price is None or price <= 0 or source_minute is None:
                    continue
                price = abs(price)
                minute_key, second_of_day, minute_text = source_minute
                scale = self._momentum_vwap_scale.get(code)
                vwap, inferred_scale, vwap_quality = infer_vwap(
                    event.get("cumulative_trade_value_raw"),
                    event.get("cumulative_volume"),
                    price,
                    scale,
                )
                if inferred_scale is not None and vwap is not None:
                    self._momentum_vwap_scale[code] = inferred_scale
                candle = self._momentum_current.get(code)
                if candle is not None and minute_key < int(candle.get("minute_key") or minute_key):
                    continue
                if candle is not None and minute_key > int(candle.get("minute_key") or minute_key):
                    changed = finalize_candle(self, code, candle) or changed
                    candle = None
                if candle is None:
                    second_in_minute = second_of_day % 60
                    partial = self._momentum_started_minute == minute_key and second_in_minute > 2
                    candle = _new_candle(
                        minute_key=minute_key,
                        second_of_day=second_of_day,
                        minute_text=minute_text,
                        trading_date=trading_date,
                        price=price,
                        vwap=vwap,
                        vwap_quality=vwap_quality,
                        phase=phase,
                        partial=partial,
                        cumulative_volume=event.get("cumulative_volume"),
                    )
                    self._momentum_current[code] = candle
                else:
                    _update_candle(candle, event, second_of_day, price, vwap, vwap_quality)

                source = {
                    **dict(self.daily_values_by_code.get(code) or {}),
                    **dict(self.quotes.get(code) or {}),
                }
                if _nested_open(source) is None and phase == "opening_burst":
                    minute_of_day = second_of_day // 60
                    if minute_of_day == regular_start:
                        values = {"day_open": price, "momentum_day_open_source": "first_regular_trade"}
                        for target in (self._quote(code), self.daily_values_by_code.setdefault(code, {})):
                            target.update(values)
            self.status["momentum_1m_raw_event_count"] = int(self.status.get("momentum_1m_raw_event_count") or 0) + len(events)
            self.status["momentum_1m_current_candle_count"] = len(self._momentum_current)
            self.status["momentum_1m_completed_candle_count"] = len(self._momentum_last_completed)
        return changed

    def stage_events(self, events):
        result = original_stage(self, events)
        changed = process_events(self, events if isinstance(events, list) else [])
        if changed:
            with self.lock:
                self._mark_daily_dirty()
            rebuild = getattr(self, "request_background_rebuild", None)
            if callable(rebuild):
                rebuild(reason="momentum_1m_signal", force=False)
        return result

    def finalize_expired(self) -> bool:
        current_minute = _system_minute_key()
        if current_minute is None:
            return False
        changed = False
        with self.lock:
            for code, candle in tuple(self._momentum_current.items()):
                if int(candle.get("minute_key") or current_minute) >= current_minute:
                    continue
                changed = finalize_candle(self, code, candle) or changed
                self._momentum_current.pop(code, None)
            if changed:
                self._mark_daily_dirty()
        return changed

    def rows(self, limit: int = 300):
        finalize_expired(self)
        result = original_rows(self, limit)
        now = datetime.now()
        session = _session(now)
        phase = str(getattr(session, "phase", "") or "")
        expected = _expected_date(now)
        current_minute = _system_minute_key(now)
        close_hold = phase in CLOSE_HOLD_PHASES
        available = 0
        vwap_available = 0
        with self.lock:
            sources = {
                normalize_code(row.get("stock_code")): {
                    **dict(self.daily_values_by_code.get(normalize_code(row.get("stock_code"))) or {}),
                    **dict(self.quotes.get(normalize_code(row.get("stock_code"))) or {}),
                }
                for row in result
                if isinstance(row, dict) and normalize_code(row.get("stock_code"))
            }
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            source = sources.get(code, {})
            if _date_digits(source.get("momentum_trading_date")) != expected:
                row["momentum_badges"] = []
                row["momentum_status"] = "unavailable_wrong_date"
                continue
            badges = []
            details = []
            for reference in ("vwap", "open"):
                signal = str(source.get(f"momentum_{reference}_signal") or "")
                minute = source.get(f"momentum_{reference}_signal_minute")
                try:
                    age = None if current_minute is None else current_minute - int(minute)
                except (TypeError, ValueError):
                    age = None
                active = bool(signal) and (close_hold or (age is not None and 0 <= age < BADGE_HOLD_MINUTES))
                if not active:
                    continue
                badges.append({"label": signal, "kind": reference, "minute": minute})
                detail = source.get(f"momentum_{reference}_detail")
                if isinstance(detail, dict):
                    details.append({"label": signal, **deepcopy(detail)})
                if reference == "vwap":
                    vwap_available += 1
            row["momentum_badges"] = badges
            row["momentum_details"] = details
            row["momentum_status"] = "close_hold" if badges and close_hold else "live" if badges else "none"
            row["momentum_closed_hold"] = bool(badges and close_hold)
            row["momentum_source"] = source.get("momentum_source") or MOMENTUM_SOURCE
            row["momentum_trading_date"] = expected
            row["momentum_sort_score"] = len(badges)
            if badges:
                available += 1
        with self.lock:
            self.status["momentum_1m_display_available_count"] = available
            self.status["momentum_1m_vwap_display_count"] = vwap_available
            self.status["momentum_1m_last_rows_at"] = now_text()
        return result

    def reset_for_date(self, target: str, phase: str):
        result = original_reset(self, target, phase) if callable(original_reset) else None
        with self.lock:
            self._momentum_current.clear()
            self._momentum_last_completed.clear()
            self._momentum_vwap_scale.clear()
            self._momentum_started_minute = _system_minute_key()
            for collection in (self.quotes, self.daily_values_by_code):
                for values in collection.values():
                    if not isinstance(values, dict):
                        continue
                    for key in PERSIST_KEYS:
                        values.pop(key, None)
            self.status["momentum_1m_rollover_at"] = now_text()
            self.status["momentum_1m_rollover_date"] = target
        return result

    state_class.__init__ = state_init
    state_class.stage_approved_trade_events = stage_events
    state_class.rows = rows
    if callable(original_reset):
        state_class.reset_approved_minute_pipeline_for_date = reset_for_date
    state_class._stockboard_momentum_1m_installed = True
