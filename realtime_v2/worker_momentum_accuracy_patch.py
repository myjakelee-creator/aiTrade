from __future__ import annotations

"""Accuracy guard for StockBoard one-minute momentum badges."""

from copy import deepcopy
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from realtime_v2.common import normalize_code, now_text, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "momentum_accuracy_guard_v1"
MOMENTUM_ACCURACY_SOURCE = "stockboard_momentum_accuracy_guard"
ACTIVE_OPEN_QUALITIES = {"current_day_ohlc_exact", "first_accepted_regular_trade"}
VERIFIED_VWAP_QUALITIES = {"verified_initial_baseline", "verified_monotonic"}
PERSIST_KEYS = (
    "momentum_accuracy_version",
    "momentum_session_day_open",
    "momentum_session_day_open_date",
    "momentum_session_day_open_source",
    "momentum_session_day_open_quality",
    "momentum_session_ohlc",
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    try:
        return float(number)
    except (TypeError, ValueError):
        return None


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _time_seconds(value: Any) -> int | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) < 6:
        return None
    try:
        hour, minute, second = int(digits[:2]), int(digits[2:4]), int(digits[4:6])
    except ValueError:
        return None
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour * 3600 + minute * 60 + second


def _clock_seconds(value: Any, fallback: str = "09:00") -> int:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text) * 3600 + int(minute_text[:2]) * 60
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 3600 + int(minute_text) * 60


def _expected_date(momentum_module) -> str:
    return _date_digits(momentum_module._expected_date())


def _started_before_regular(session) -> bool:
    windows = getattr(session, "windows", {}) or {}
    start = _clock_seconds(windows.get("regular_start"), "09:00")
    now = datetime.now()
    return now.hour * 3600 + now.minute * 60 + now.second < start


def _dated_ohlc_open(source: dict[str, Any], expected: str) -> dict[str, Any] | None:
    for key in ("momentum_session_ohlc", "display_ohlc", "realtime_ohlc", "ohlc"):
        nested = source.get(key)
        if not isinstance(nested, dict):
            continue
        nested_date = _date_digits(
            nested.get("date")
            or nested.get("source_trading_date")
            or source.get("ohlc_trading_date")
        )
        value = _number(nested.get("open"))
        if nested_date != expected or value is None or value <= 0:
            continue
        quality = str(
            nested.get("open_quality")
            or nested.get("quality")
            or source.get("momentum_session_day_open_quality")
            or ""
        )
        if key == "momentum_session_ohlc" and quality not in ACTIVE_OPEN_QUALITIES:
            continue
        return {
            "value": abs(value),
            "date": expected,
            "source": str(nested.get("open_source") or nested.get("source") or f"{key}.open"),
            "quality": quality if key == "momentum_session_ohlc" else "current_day_ohlc_exact",
        }
    direct = _number(source.get("day_open"))
    direct_date = _date_digits(source.get("day_open_trading_date"))
    if direct is not None and direct > 0 and direct_date == expected:
        return {
            "value": abs(direct),
            "date": expected,
            "source": str(source.get("day_open_source") or "dated_day_open"),
            "quality": "current_day_ohlc_exact",
        }
    return None


def _strict_open_meta(source: dict[str, Any], expected: str) -> dict[str, Any] | None:
    value = _number(source.get("momentum_session_day_open"))
    date_text = _date_digits(source.get("momentum_session_day_open_date"))
    quality = str(source.get("momentum_session_day_open_quality") or "")
    if value is not None and value > 0 and date_text == expected and quality in ACTIVE_OPEN_QUALITIES:
        return {
            "value": abs(value),
            "date": expected,
            "source": str(source.get("momentum_session_day_open_source") or "momentum_session_day_open"),
            "quality": quality,
        }
    return _dated_ohlc_open(source, expected)


def _top100_metadata(state) -> dict[str, dict[str, Any]]:
    with state.lock:
        rows = {
            normalize_code(code): dict(row)
            for code, row in state.quotes.items()
            if normalize_code(code) and isinstance(row, dict)
        }

    def rank_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
        code, row = item
        for field in ("candidate_rank", "model_rank", "pool_rank", "rank", "seed_rank"):
            rank = _number(row.get(field))
            if rank is not None and rank > 0:
                return (0, int(rank), -(_number(row.get("trade_value_eok")) or 0.0), code)
        return (1, 999999, -(_number(row.get("trade_value_eok")) or 0.0), code)

    ranked = sorted(rows.items(), key=rank_key)[:100]
    with state.lock:
        state.status["momentum_accuracy_current_top100_count"] = len(ranked)
    return {
        code: {
            "stock_name": row.get("stock_name") or state.name_by_code.get(code) or code,
            "rank": index,
        }
        for index, (code, row) in enumerate(ranked, start=1)
    }


def install(base) -> None:
    """Install strict momentum evaluation without adding a source or thread."""

    from realtime_v2 import worker_momentum_1m_patch as momentum
    from realtime_v2 import worker_momentum_badge_policy_patch as policy
    from realtime_v2.momentum_badge_engine import MomentumBadgeEngine, _minute

    state_class = getattr(base, "State", None)
    handler_class = getattr(base, "WebHandler", None)
    if state_class is None or handler_class is None or getattr(
        state_class, "_stockboard_momentum_accuracy_installed", False
    ):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *PERSIST_KEYS))
    )

    original_state_init = state_class.__init__
    original_apply_trade = state_class._apply_trade
    original_reset = getattr(state_class, "reset_approved_minute_pipeline_for_date", None)
    original_do_get = handler_class.do_GET
    original_finalize = momentum.finalize_candle
    original_detail = momentum._detail

    def strict_nested_open(source: dict[str, Any]) -> float | None:
        expected = _expected_date(momentum)
        meta = _strict_open_meta(source if isinstance(source, dict) else {}, expected)
        return None if meta is None else float(meta["value"])

    def accuracy_detail(
        candle: dict[str, Any],
        previous: dict[str, Any] | None,
        day_open: float | None,
    ) -> dict[str, Any]:
        detail = original_detail(candle, previous, day_open)
        detail.update(
            {
                "previous_minute_key": previous.get("minute_key") if isinstance(previous, dict) else None,
                "consecutive_previous": bool(
                    isinstance(previous, dict)
                    and int(candle.get("minute_key") or 0)
                    == int(previous.get("minute_key") or -2) + 1
                ),
                "day_open_date": candle.get("day_open_date"),
                "day_open_source": candle.get("day_open_source"),
                "day_open_quality": candle.get("day_open_quality"),
                "vwap_valid": bool(candle.get("vwap_valid")),
                "vwap_quality": candle.get("vwap_quality"),
                "vwap_invalid_reason": candle.get("vwap_invalid_reason"),
                "vwap_source_scope": candle.get("vwap_source_scope"),
                "vwap_source_exchange": candle.get("vwap_source_exchange"),
            }
        )
        return detail

    def detect_vwap_signal(
        current: dict[str, Any], previous: dict[str, Any] | None
    ) -> str | None:
        if not bool(current.get("vwap_valid")):
            return None
        close = _number(current.get("close"))
        open_price = _number(current.get("open"))
        high = _number(current.get("high"))
        low = _number(current.get("low"))
        vwap = _number(current.get("vwap"))
        if None in (close, open_price, high, low, vwap):
            return None
        consecutive = bool(
            isinstance(previous, dict)
            and int(current.get("minute_key") or 0)
            == int(previous.get("minute_key") or -2) + 1
        )
        if consecutive and bool(previous.get("vwap_valid")):
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

    def finalize_candle(self, code: str, candle: dict[str, Any]) -> bool:
        previous = self._momentum_last_completed.get(code)
        source = {
            **dict(self.daily_values_by_code.get(code) or {}),
            **dict(self.quotes.get(code) or {}),
        }
        expected = _date_digits(candle.get("trading_date")) or _expected_date(momentum)
        open_meta = _strict_open_meta(source, expected)
        candle["day_open"] = open_meta.get("value") if open_meta else None
        candle["day_open_date"] = open_meta.get("date") if open_meta else None
        candle["day_open_source"] = open_meta.get("source") if open_meta else None
        candle["day_open_quality"] = open_meta.get("quality") if open_meta else None
        candle["previous_minute_key"] = previous.get("minute_key") if isinstance(previous, dict) else None
        candle["previous_close"] = previous.get("close") if isinstance(previous, dict) else None
        candle["previous_vwap"] = previous.get("vwap") if isinstance(previous, dict) else None
        candle["previous_vwap_valid"] = bool(previous.get("vwap_valid")) if isinstance(previous, dict) else False
        candle["consecutive_previous"] = bool(
            isinstance(previous, dict)
            and int(candle.get("minute_key") or 0)
            == int(previous.get("minute_key") or -2) + 1
        )
        changed = original_finalize(self, code, candle)
        for target in (self._quote(code), self.daily_values_by_code.setdefault(code, {})):
            target["momentum_accuracy_version"] = PATCH_VERSION
        return changed

    def strict_process_events(self, events: list[dict[str, Any]]) -> bool:
        changed = False
        session = momentum._session()
        trading_date = _date_digits(
            getattr(session, "trading_date", "")
            or getattr(session, "calendar_date", "")
        )
        phase = str(getattr(session, "phase", "") or "")
        with self.lock:
            for event in events:
                if not isinstance(event, dict):
                    continue
                code = normalize_code(event.get("stock_code"))
                price = _number(event.get("execution_strength_trade_price"))
                source_minute = momentum._source_minute(event, trading_date)
                if not code or price is None or price <= 0 or source_minute is None:
                    continue
                price = abs(price)
                minute_key, second_of_day, minute_text = source_minute
                raw_value = _number(event.get("cumulative_trade_value_raw"))
                raw_volume = _number(event.get("cumulative_volume"))
                raw_value = abs(raw_value) if raw_value is not None else None
                raw_volume = abs(raw_volume) if raw_volume is not None else None
                scope = str(event.get("raw_item") or code)
                exchange = str(event.get("execution_strength_exchange") or "")
                tracker = self._momentum_accuracy_vwap_tracker.get(code)
                invalid_reason = None
                if raw_value is None or raw_volume is None or raw_value <= 0 or raw_volume <= 0:
                    invalid_reason = "missing_or_nonpositive_cumulative"
                elif isinstance(tracker, dict) and tracker.get("trading_date") == trading_date:
                    if str(tracker.get("scope") or "") != scope:
                        invalid_reason = "source_scope_changed"
                    elif raw_value < float(tracker.get("cumulative_value") or 0.0):
                        invalid_reason = "cumulative_value_decreased"
                    elif raw_volume < float(tracker.get("cumulative_volume") or 0.0):
                        invalid_reason = "cumulative_volume_decreased"

                if invalid_reason:
                    self.status["momentum_accuracy_vwap_reject_count"] = int(
                        self.status.get("momentum_accuracy_vwap_reject_count") or 0
                    ) + 1
                    self.status["momentum_accuracy_last_vwap_reject"] = {
                        "stock_code": code,
                        "reason": invalid_reason,
                        "scope": scope,
                        "exchange": exchange,
                        "at": now_text(),
                    }
                    self._momentum_accuracy_vwap_tracker[code] = {
                        "trading_date": trading_date,
                        "scope": scope,
                        "exchange": exchange,
                        "cumulative_value": raw_value,
                        "cumulative_volume": raw_volume,
                    }
                    vwap = None
                    vwap_quality = invalid_reason
                else:
                    scale = self._momentum_vwap_scale.get(code)
                    vwap, inferred_scale, infer_quality = momentum.infer_vwap(
                        raw_value, raw_volume, price, scale
                    )
                    if inferred_scale is not None and vwap is not None:
                        self._momentum_vwap_scale[code] = inferred_scale
                    verified = (
                        vwap is not None
                        and infer_quality in {"validated_existing_scale", "validated_inferred_scale"}
                    )
                    vwap_quality = (
                        "verified_initial_baseline"
                        if verified and not isinstance(tracker, dict)
                        else "verified_monotonic"
                        if verified
                        else infer_quality
                    )
                    if not verified:
                        vwap = None
                        invalid_reason = str(infer_quality or "vwap_unverified")
                        self.status["momentum_accuracy_vwap_reject_count"] = int(
                            self.status.get("momentum_accuracy_vwap_reject_count") or 0
                        ) + 1
                    self._momentum_accuracy_vwap_tracker[code] = {
                        "trading_date": trading_date,
                        "scope": scope,
                        "exchange": exchange,
                        "cumulative_value": raw_value,
                        "cumulative_volume": raw_volume,
                    }

                candle = self._momentum_current.get(code)
                if candle is not None and minute_key < int(candle.get("minute_key") or minute_key):
                    continue
                if candle is not None and minute_key > int(candle.get("minute_key") or minute_key):
                    changed = finalize_candle(self, code, candle) or changed
                    candle = None

                if candle is None:
                    second_in_minute = second_of_day % 60
                    partial = self._momentum_started_minute == minute_key and second_in_minute > 2
                    candle = momentum._new_candle(
                        minute_key=minute_key,
                        second_of_day=second_of_day,
                        minute_text=minute_text,
                        trading_date=trading_date,
                        price=price,
                        vwap=vwap,
                        vwap_quality=vwap_quality,
                        phase=phase,
                        partial=partial,
                        cumulative_volume=raw_volume,
                    )
                    candle.update(
                        {
                            "vwap_valid": vwap is not None and vwap_quality in VERIFIED_VWAP_QUALITIES,
                            "vwap_source_scope": scope,
                            "vwap_source_exchange": exchange,
                            "cumulative_trade_value_raw": raw_value,
                            "vwap_invalid_reason": invalid_reason,
                            "_vwap_tainted": bool(invalid_reason),
                        }
                    )
                    self._momentum_current[code] = candle
                else:
                    if str(candle.get("vwap_source_scope") or scope) != scope:
                        invalid_reason = "candle_scope_changed"
                    if invalid_reason:
                        candle["_vwap_tainted"] = True
                        candle["vwap_valid"] = False
                        candle["vwap"] = None
                        candle["vwap_invalid_reason"] = invalid_reason
                    momentum._update_candle(
                        candle,
                        event,
                        second_of_day,
                        price,
                        None if candle.get("_vwap_tainted") else vwap,
                        vwap_quality,
                    )
                    candle["cumulative_trade_value_raw"] = raw_value
                    candle["vwap_source_exchange"] = exchange
                    if (
                        not candle.get("_vwap_tainted")
                        and vwap is not None
                        and vwap_quality in VERIFIED_VWAP_QUALITIES
                    ):
                        candle["vwap_valid"] = True
                        candle["vwap_quality"] = vwap_quality

            self.status["momentum_1m_raw_event_count"] = int(
                self.status.get("momentum_1m_raw_event_count") or 0
            ) + len(events)
            self.status["momentum_1m_current_candle_count"] = len(self._momentum_current)
            self.status["momentum_1m_completed_candle_count"] = len(self._momentum_last_completed)
            self.status["momentum_accuracy_verified_vwap_candle_count"] = sum(
                1
                for candle in self._momentum_current.values()
                if isinstance(candle, dict) and candle.get("vwap_valid")
            )
        return changed

    def engine_observe(
        self,
        code: Any,
        current: dict[str, Any] | None,
        *,
        day_open: Any,
        trading_date: str,
        legacy_signals: dict[str, Any] | None = None,
    ) -> bool:
        normalized = normalize_code(code)
        if not self.config["enabled"] or not normalized or not isinstance(current, dict):
            return False
        current_minute = _minute(current.get("minute_key"))
        if current_minute is None or bool(current.get("partial")):
            return False
        previous = self.last_candle_by_code.get(normalized)
        if previous is not None and _minute(previous.get("minute_key")) == current_minute:
            return False
        previous_minute = _minute(previous.get("minute_key")) if isinstance(previous, dict) else None
        consecutive = bool(previous_minute is not None and previous_minute + 1 == current_minute)
        previous_context = previous if consecutive else None
        values = self._context(current, previous_context, day_open)
        if not bool(current.get("vwap_valid")):
            values["V"] = None
        if not (
            consecutive
            and isinstance(previous_context, dict)
            and bool(previous_context.get("vwap_valid"))
        ):
            values["V1"] = None

        state = self.states.setdefault(normalized, {})
        changed = False
        for reference in self.config["reference_order"]:
            triggered = next(
                (
                    rule
                    for rule in self.rules_by_reference[reference]
                    if rule.triggers(values, consecutive)
                ),
                None,
            )
            existing = state.get(reference)
            if triggered is not None:
                next_state, state_changed = self._activate(
                    reference, triggered, existing, current_minute, trading_date
                )
                state[reference] = next_state
                changed = state_changed or changed
                continue

            existing_rule = (
                self.rule_by_id.get(str(existing.get("rule_id") or ""))
                if isinstance(existing, dict)
                else None
            )
            if existing_rule is not None and existing_rule.stays(values):
                next_state, state_changed = self._activate(
                    reference, existing_rule, existing, current_minute, trading_date
                )
                state[reference] = next_state
                changed = state_changed or changed
                continue

            if isinstance(existing, dict) and existing.get("exit_minute") is None:
                exit_minute = current_minute
                state[reference] = {
                    **existing,
                    "exit_minute": exit_minute,
                    "fade_start_minute": exit_minute + int(self.config["exit_hold_minutes"]),
                    "expires_minute": exit_minute
                    + int(self.config["exit_hold_minutes"])
                    + int(self.config["fade_minutes"]),
                }
                changed = True

        if not state:
            self.states.pop(normalized, None)
        self.last_candle_by_code[normalized] = deepcopy(current)
        if changed:
            self.version += 1
        return changed

    def active_badges(self, code: Any, current_minute: int | None):
        normalized = normalize_code(code)
        result: list[dict[str, Any]] = []
        references = self.states.get(normalized) or {}
        for reference in self.config["reference_order"]:
            state = references.get(reference)
            if not isinstance(state, dict) or state.get("exit_minute") is not None:
                continue
            result.append(
                {
                    "id": state.get("rule_id"),
                    "reference": reference,
                    "badge": state.get("badge"),
                    "label": state.get("label"),
                    "tone": state.get("tone"),
                    "phase": "active",
                    "signal_minute": state.get("signal_minute"),
                    "last_matched_minute": state.get("last_matched_minute"),
                }
            )
        return result

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        session = market_session_now()
        self._momentum_accuracy_started_before_open = _started_before_regular(session)
        self._momentum_accuracy_vwap_tracker: dict[str, dict[str, Any]] = {}
        migrated = 0
        with self.lock:
            engine = getattr(self, "_momentum_badge_engine", None)
            if engine is not None:
                engine.states.clear()
                engine.last_candle_by_code.clear()
                engine.version += 1
            if hasattr(self, "_momentum_last_completed"):
                self._momentum_last_completed.clear()
            if hasattr(self, "_momentum_current"):
                self._momentum_current.clear()
            for collection in (self.quotes, self.daily_values_by_code):
                for values in collection.values():
                    if not isinstance(values, dict):
                        continue
                    if values.get("momentum_accuracy_version") == PATCH_VERSION:
                        continue
                    for key in (
                        "momentum_badge_state",
                        "momentum_open_signal",
                        "momentum_open_signal_minute",
                        "momentum_open_signal_at",
                        "momentum_open_detail",
                        "momentum_vwap_signal",
                        "momentum_vwap_signal_minute",
                        "momentum_vwap_signal_at",
                        "momentum_vwap_detail",
                        "momentum_last_completed_candle",
                    ):
                        values.pop(key, None)
                    values["momentum_accuracy_version"] = PATCH_VERSION
                    migrated += 1
            if migrated:
                self._mark_daily_dirty()
            self.status.update(
                {
                    "momentum_accuracy_installed": True,
                    "momentum_accuracy_version": PATCH_VERSION,
                    "momentum_accuracy_migrated_value_count": migrated,
                    "momentum_accuracy_current_day_open_required": True,
                    "momentum_accuracy_consecutive_cross_required": True,
                    "momentum_accuracy_verified_vwap_required": True,
                    "momentum_accuracy_active_only_display": True,
                    "momentum_accuracy_alert_scope": "current_top100",
                    "momentum_accuracy_extra_qax_fids": 0,
                    "momentum_accuracy_extra_rest_requests": 0,
                    "momentum_accuracy_extra_websockets": 0,
                    "momentum_accuracy_extra_threads": 0,
                }
            )

    def apply_trade(self, event: dict[str, Any]) -> None:
        before = int(self.status.get("trade_count") or 0)
        original_apply_trade(self, event)
        after = int(self.status.get("trade_count") or 0)
        if after <= before:
            return

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
        quote = self.quotes.get(code)
        if not isinstance(quote, dict):
            return
        price = _number(quote.get("price") or quote.get("trade_price"))
        if price is None or price <= 0:
            return

        session = market_session_now()
        expected = _date_digits(
            getattr(session, "trading_date", "")
            or getattr(session, "calendar_date", "")
        )
        phase = str(getattr(session, "phase", "") or "")
        if phase not in {"regular", "closing_call", "after_wait", "aftermarket"}:
            return

        daily = self.daily_values_by_code.setdefault(code, {})
        source = {**dict(daily), **dict(quote)}
        exact_meta = _dated_ohlc_open(source, expected)
        trade_time = raw.get("trade_time_raw") or values.get("trade_time") or quote.get("trade_time")
        trade_seconds = _time_seconds(trade_time)
        open_meta = exact_meta
        if open_meta is None:
            existing = _strict_open_meta(source, expected)
            if existing is not None:
                open_meta = existing
            elif self._momentum_accuracy_started_before_open:
                windows = getattr(session, "windows", {}) or {}
                regular_start = _clock_seconds(windows.get("regular_start"), "09:00")
                if trade_seconds is not None and trade_seconds >= regular_start:
                    open_meta = {
                        "value": abs(price),
                        "date": expected,
                        "source": "first_accepted_regular_trade",
                        "quality": "first_accepted_regular_trade",
                    }

        existing_ohlc = quote.get("momentum_session_ohlc")
        if not isinstance(existing_ohlc, dict) or _date_digits(existing_ohlc.get("date")) != expected:
            initial_open = float(open_meta["value"]) if open_meta is not None else abs(price)
            quality = str(open_meta.get("quality")) if open_meta is not None else "partial_after_restart"
            source_name = str(open_meta.get("source")) if open_meta is not None else "first_accepted_after_restart"
            session_ohlc = {
                "open": initial_open,
                "high": abs(price),
                "low": abs(price),
                "close": abs(price),
                "date": expected,
                "source_trading_date": expected,
                "open_source": source_name,
                "open_quality": quality,
                "source": MOMENTUM_ACCURACY_SOURCE,
            }
        else:
            session_ohlc = dict(existing_ohlc)
            if (
                open_meta is not None
                and str(session_ohlc.get("open_quality") or "") not in ACTIVE_OPEN_QUALITIES
            ):
                session_ohlc["open"] = float(open_meta["value"])
                session_ohlc["open_source"] = str(open_meta["source"])
                session_ohlc["open_quality"] = str(open_meta["quality"])
            session_ohlc["high"] = max(_number(session_ohlc.get("high")) or abs(price), abs(price))
            session_ohlc["low"] = min(_number(session_ohlc.get("low")) or abs(price), abs(price))
            session_ohlc["close"] = abs(price)

        persist = {
            "momentum_accuracy_version": PATCH_VERSION,
            "momentum_session_ohlc": session_ohlc,
        }
        if open_meta is not None:
            persist.update(
                {
                    "momentum_session_day_open": float(open_meta["value"]),
                    "momentum_session_day_open_date": expected,
                    "momentum_session_day_open_source": str(open_meta["source"]),
                    "momentum_session_day_open_quality": str(open_meta["quality"]),
                }
            )
        for target in (quote, daily):
            target.update(deepcopy(persist))
        self._mark_daily_dirty()

    def reset_for_date(self, target: str, phase: str):
        result = original_reset(self, target, phase) if callable(original_reset) else None
        with self.lock:
            self._momentum_accuracy_vwap_tracker.clear()
            for collection in (self.quotes, self.daily_values_by_code):
                for values in collection.values():
                    if not isinstance(values, dict):
                        continue
                    for key in PERSIST_KEYS[1:]:
                        values.pop(key, None)
                    values["momentum_accuracy_version"] = PATCH_VERSION
            self.status["momentum_accuracy_rollover_at"] = now_text()
            self.status["momentum_accuracy_rollover_date"] = target
        return result

    def diagnostics(self, codes=None, limit: int = 20) -> dict[str, Any]:
        requested = [normalize_code(code) for code in (codes or []) if normalize_code(code)]
        metadata = _top100_metadata(self)
        if not requested:
            requested = list(metadata)[: max(1, min(100, int(limit or 20)))]
        rows = []
        with self.lock:
            engine = self._momentum_badge_engine
            for code in requested[: max(1, min(100, int(limit or 20)))]:
                source = {
                    **dict(self.daily_values_by_code.get(code) or {}),
                    **dict(self.quotes.get(code) or {}),
                }
                candle = source.get("momentum_last_completed_candle")
                if not isinstance(candle, dict):
                    candle = self._momentum_last_completed.get(code)
                candle = dict(candle) if isinstance(candle, dict) else {}
                expected = _expected_date(momentum)
                open_meta = _strict_open_meta(source, expected)
                previous = None
                if candle.get("previous_minute_key") is not None:
                    previous = {
                        "minute_key": candle.get("previous_minute_key"),
                        "close": candle.get("previous_close"),
                        "vwap": candle.get("previous_vwap"),
                        "vwap_valid": bool(candle.get("previous_vwap_valid")),
                    }
                context = engine._context(
                    candle,
                    previous if candle.get("consecutive_previous") else None,
                    open_meta.get("value") if open_meta else None,
                )
                states = deepcopy(engine.states.get(code) or {})
                evaluations = []
                for reference, state in states.items():
                    rule = engine.rule_by_id.get(str(state.get("rule_id") or ""))
                    evaluations.append(
                        {
                            "reference": reference,
                            "rule_id": state.get("rule_id"),
                            "badge": state.get("badge"),
                            "internal_exit_minute": state.get("exit_minute"),
                            "display_active": state.get("exit_minute") is None,
                            "trigger_matches": (
                                rule.triggers(
                                    context,
                                    bool(candle.get("consecutive_previous")),
                                )
                                if rule is not None
                                else False
                            ),
                            "stay_matches": rule.stays(context) if rule is not None else False,
                        }
                    )
                rows.append(
                    {
                        "stock_code": code,
                        "stock_name": metadata.get(code, {}).get("stock_name")
                        or source.get("stock_name")
                        or code,
                        "rank": metadata.get(code, {}).get("rank"),
                        "badges": engine.badges(code, momentum._system_minute_key()),
                        "internal_states": evaluations,
                        "minute_key": candle.get("minute_key"),
                        "minute_text": candle.get("minute_text"),
                        "open": candle.get("open"),
                        "high": candle.get("high"),
                        "low": candle.get("low"),
                        "close": candle.get("close"),
                        "previous_minute_key": candle.get("previous_minute_key"),
                        "previous_close": candle.get("previous_close"),
                        "consecutive_previous": candle.get("consecutive_previous"),
                        "day_open": open_meta.get("value") if open_meta else None,
                        "day_open_date": open_meta.get("date") if open_meta else None,
                        "day_open_source": open_meta.get("source") if open_meta else None,
                        "day_open_quality": open_meta.get("quality") if open_meta else None,
                        "vwap": candle.get("vwap"),
                        "previous_vwap": candle.get("previous_vwap"),
                        "vwap_valid": candle.get("vwap_valid"),
                        "vwap_quality": candle.get("vwap_quality"),
                        "vwap_invalid_reason": candle.get("vwap_invalid_reason"),
                        "vwap_source_scope": candle.get("vwap_source_scope"),
                        "vwap_source_exchange": candle.get("vwap_source_exchange"),
                    }
                )
        return {
            "schema_version": 1,
            "source": MOMENTUM_ACCURACY_SOURCE,
            "version": PATCH_VERSION,
            "ts": now_text(),
            "count": len(rows),
            "rows": rows,
        }

    def do_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/v2/momentum_diagnostics":
            query = parse_qs(parsed.query)
            raw_codes = ",".join(query.get("codes") or [])
            codes = [part.strip() for part in raw_codes.split(",") if part.strip()]
            try:
                limit = int((query.get("limit") or ["20"])[0])
            except (TypeError, ValueError):
                limit = 20
            self._json(
                self.server.state.momentum_accuracy_diagnostics(
                    codes=codes,
                    limit=limit,
                )
            )
            return
        return original_do_get(self)

    momentum._nested_open = strict_nested_open
    momentum._detail = accuracy_detail
    momentum.detect_vwap_signal = detect_vwap_signal
    momentum.finalize_candle = finalize_candle
    momentum.process_events = strict_process_events
    MomentumBadgeEngine.observe_completed_candle = engine_observe
    MomentumBadgeEngine.badges = active_badges
    policy._legacy_signals = lambda _source: {}
    policy._metadata_by_code = _top100_metadata

    state_class.__init__ = state_init
    state_class._apply_trade = apply_trade
    state_class.momentum_accuracy_diagnostics = diagnostics
    if callable(original_reset):
        state_class.reset_approved_minute_pipeline_for_date = reset_for_date
    handler_class.do_GET = do_get
    state_class._stockboard_momentum_accuracy_installed = True
