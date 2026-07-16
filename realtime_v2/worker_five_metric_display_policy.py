from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Callable

from realtime_v2.common import normalize_code, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "five_metric_display_policy_v1"
ACTIVE_PHASES = {
    "premarket",
    "opening_call",
    "opening_burst",
    "regular",
    "closing_call",
    "after_wait",
    "aftermarket",
}
HOLD_PHASES = {"closed", "before_market", "weekend", "holiday"}


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _age_sec(value: Any, now: datetime | None = None) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    current = now or datetime.now().astimezone()
    if parsed.tzinfo is None and current.tzinfo is not None:
        parsed = parsed.replace(tzinfo=current.tzinfo)
    elif parsed.tzinfo is not None and current.tzinfo is None:
        current = current.astimezone()
    try:
        return max(0.0, (current - parsed).total_seconds())
    except TypeError:
        return None


def _contains_source(*tokens: str) -> Callable[[str], bool]:
    lowered = tuple(token.lower() for token in tokens)

    def allowed(source: str) -> bool:
        value = str(source or "").lower()
        return bool(value) and any(token in value for token in lowered)

    return allowed


def _orderbook_live_source(row: dict[str, Any]) -> bool:
    if row.get("orderbook_live") is True:
        return True
    source = str(row.get("orderbook_source") or "").lower()
    return bool(source) and "rest_lowload" not in source and any(
        token in source
        for token in (
            "qax_realtime_orderbook",
            "realtime_orderbook",
            "websocket_orderbook",
            "ws_orderbook",
        )
    )


def _positive(row: dict[str, Any], key: str) -> bool:
    value = _number(row.get(key))
    return value is not None and value > 0


def _numeric(row: dict[str, Any], key: str) -> bool:
    return _number(row.get(key)) is not None


def _rank_limit(row: dict[str, Any], top20: float, pool: float) -> float:
    rank = _number(row.get("rank"))
    return top20 if rank is not None and 1 <= rank <= 20 else pool


POLICIES: dict[str, dict[str, Any]] = {
    "orderbook": {
        "value_key": "bid_ask_ratio",
        "display_keys": (
            "bid_ask_ratio",
            "bid_pct",
            "ask_pct",
            "bid_volume",
            "ask_volume",
            "best_ask_price",
            "best_bid_price",
        ),
        "source_key": "orderbook_source",
        "date_keys": (
            "orderbook_source_trading_date",
            "_session_hold_orderbook_date",
        ),
        "time_keys": ("orderbook_received_at", "last_valid_orderbook_at"),
        "status_key": "orderbook_status",
        "available_key": "orderbook_available",
        "basis_key": "orderbook_display_basis",
        "age_key": "orderbook_age_sec",
        "active_source": lambda row: _orderbook_live_source(row),
        "hold_source": lambda row: bool(
            str(row.get("orderbook_source") or "")
            or _positive(row, "bid_ask_ratio")
        ),
        "value_valid": lambda row: _positive(row, "bid_ask_ratio"),
        "max_age": lambda row: 3.0,
        "active_basis": "fresh_live_orderbook",
        "hold_basis": "previous_session_final_until_premarket",
    },
    "execution": {
        "value_key": "execution_strength",
        "display_keys": ("execution_strength",),
        "source_key": "execution_strength_source",
        "date_keys": (
            "execution_source_trading_date",
            "_session_hold_execution_date",
        ),
        "time_keys": (
            "execution_strength_received_at",
            "execution_strength_updated_at",
            "last_valid_strength_at",
        ),
        "status_key": "execution_strength_status",
        "available_key": "execution_strength_available",
        "basis_key": "execution_strength_display_basis",
        "age_key": "execution_strength_age_sec",
        "active_source": lambda row: str(
            row.get("execution_strength_source") or ""
        ) == "kiwoom_rest_ws_0B_fid228",
        "hold_source": _contains_source("kiwoom_rest_ws_0b_fid228"),
        "value_valid": lambda row: _positive(row, "execution_strength"),
        "max_age": lambda row: 20.0,
        "active_basis": "fresh_fid228_websocket",
        "hold_basis": "previous_session_final_until_premarket",
    },
    "strength5": {
        "value_key": "strength_5m",
        "display_keys": ("strength_5m", "strength_20m", "strength_60m"),
        "source_key": "strength_source",
        "date_keys": (
            "strength_source_trading_date",
            "_session_hold_strength5_date",
        ),
        "time_keys": ("strength_snapshot_at", "last_valid_strength_at"),
        "status_key": "strength_status",
        "available_key": "strength5_available",
        "basis_key": "strength_display_basis",
        "age_key": "strength5_age_sec",
        "active_source": lambda row: _contains_source("ka10046_rest_lowload")(
            str(row.get("strength_source") or "")
        ),
        "hold_source": lambda row: _contains_source(
            "ka10046_rest_lowload", "opt10046"
        )(str(row.get("strength_source") or "")),
        "value_valid": lambda row: _positive(row, "strength_5m"),
        "max_age": lambda row: _rank_limit(row, 180.0, 600.0),
        "active_basis": "current_session_ka10046_snapshot",
        "hold_basis": "previous_session_final_until_premarket",
    },
    "program": {
        "value_key": "program_net",
        "display_keys": ("program_net",),
        "source_key": "program_net_source",
        "date_keys": (
            "program_source_trading_date",
            "_session_hold_program_date",
        ),
        "time_keys": ("program_net_updated_at",),
        "status_key": "program_net_status",
        "available_key": "program_available",
        "basis_key": "program_display_basis",
        "age_key": "program_age_sec",
        "active_source": lambda row: _contains_source("ka90004")(
            str(row.get("program_net_source") or "")
        ),
        "hold_source": lambda row: _contains_source("ka90004")(
            str(row.get("program_net_source") or "")
        ),
        "value_valid": lambda row: _numeric(row, "program_net"),
        "max_age": lambda row: 300.0,
        "active_basis": "current_session_ka90004",
        "hold_basis": "previous_session_final_until_premarket",
    },
    "large_trade": {
        "value_key": "large_trade_net_count",
        "display_keys": (
            "large_trade_buy_count",
            "large_trade_sell_count",
            "large_trade_net_count",
            "large_trade_buy_sum_eok",
            "large_trade_sell_sum_eok",
            "large_trade_net_sum_eok",
        ),
        "source_key": "large_trade_source",
        "date_keys": (
            "large_trade_source_trading_date",
            "large_trade_trading_date",
            "_session_hold_large_trade_date",
        ),
        "time_keys": ("large_trade_updated_at",),
        "status_key": "large_trade_status",
        "available_key": "large_trade_available",
        "basis_key": "large_trade_display_basis",
        "age_key": "large_trade_age_sec",
        "active_source": lambda row: _contains_source(
            "ka10055_rest_incremental", "collector_aggregate"
        )(str(row.get("large_trade_source") or "")),
        "hold_source": lambda row: _contains_source(
            "ka10055_rest_incremental", "collector_aggregate"
        )(str(row.get("large_trade_source") or "")),
        "value_valid": lambda row: _numeric(row, "large_trade_net_count"),
        "max_age": lambda row: _rank_limit(row, 600.0, 1200.0),
        "active_basis": "current_session_valid_large_trade_source",
        "hold_basis": "previous_session_final_until_premarket",
    },
}


def _first_date(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _date_digits(row.get(key))
        if value:
            return value
    return ""


def _first_age(row: dict[str, Any], keys: tuple[str, ...], now: datetime) -> float | None:
    for key in keys:
        age = _age_sec(row.get(key), now)
        if age is not None:
            return age
    return None


def _clear(row: dict[str, Any], policy: dict[str, Any], status: str, age: float | None) -> None:
    for key in policy["display_keys"]:
        row.pop(key, None)
    row[policy["available_key"]] = False
    row[policy["status_key"]] = status
    row[policy["basis_key"]] = "hidden_by_five_metric_policy"
    row[policy["age_key"]] = None if age is None else round(age, 3)


def _event_values(base, event: dict[str, Any]) -> dict[str, Any]:
    merged = getattr(base, "merged_event_values", None)
    if callable(merged):
        result = merged(event)
        return result if isinstance(result, dict) else {}
    result: dict[str, Any] = {}
    if isinstance(event.get("values"), dict):
        result.update(event["values"])
    if isinstance(event.get("kwargs"), dict):
        result.update(event["kwargs"])
    return result


def install(base) -> None:
    """Apply one final display/hold contract to five auxiliary metrics.

    This module does not add a collector, thread, REST request, WebSocket connection,
    calculation loop, or browser-side metric. It validates source, trading date and
    freshness immediately before rows leave the worker. During active sessions a value
    is shown only when its approved current-session source is fresh. During closed and
    before-market phases the lifecycle layer's verified final value is preserved until
    the next premarket boundary.
    """

    import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_five_metric_display_policy_installed",
        False,
    ):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys(
            (
                *getattr(base, "DAILY_PERSIST_KEYS", ()),
                "orderbook_source",
                "orderbook_status",
                "orderbook_live",
                "orderbook_source_trading_date",
                "_session_hold_orderbook_date",
            )
        )
    )

    original_state_init = state_class.__init__
    original_rows = state_class.rows
    original_apply_orderbook = getattr(state_class, "_apply_orderbook", None)

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["five_metric_display_policy_installed"] = True
            self.status["five_metric_display_policy_version"] = PATCH_VERSION

    def apply_orderbook(self, event: dict[str, Any]) -> None:
        if callable(original_apply_orderbook):
            original_apply_orderbook(self, event)
        values = _event_values(base, event)
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return
        now = datetime.now()
        session = market_session_now(now)
        source_date = _date_digits(session.trading_date or session.calendar_date)
        received_at = event.get("ts") or values.get("orderbook_received_at")
        with self.lock:
            quote = self.quotes.get(code)
            daily = self.daily_values_by_code.setdefault(code, {})
            for target in (quote, daily):
                if not isinstance(target, dict):
                    continue
                target["orderbook_source"] = "qax_realtime_orderbook"
                target["orderbook_status"] = "ok"
                target["orderbook_live"] = True
                if received_at not in (None, ""):
                    target["orderbook_received_at"] = received_at
                if source_date:
                    target["orderbook_source_trading_date"] = source_date
                    target["_session_hold_orderbook_date"] = source_date
            self.status["orderbook_source_trading_date"] = source_date or None
            self._mark_daily_dirty()

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        now = datetime.now().astimezone()
        session = market_session_now(now)
        phase = str(session.phase or "")
        expected = lifecycle._expected_date(session, now)
        active = phase in ACTIVE_PHASES or phase not in HOLD_PHASES
        mode = "active" if active else "hold"
        counters = {
            metric: {
                "visible": 0,
                "missing": 0,
                "date": 0,
                "source": 0,
                "stale": 0,
            }
            for metric in POLICIES
        }

        for row in result:
            if not isinstance(row, dict) or not normalize_code(row.get("stock_code")):
                continue
            for metric, policy in POLICIES.items():
                age = _first_age(row, policy["time_keys"], now)
                if not policy["value_valid"](row):
                    _clear(row, policy, f"{metric}_missing_hidden", age)
                    counters[metric]["missing"] += 1
                    continue
                source_date = _first_date(row, policy["date_keys"])
                if not expected or source_date != expected:
                    _clear(row, policy, f"{metric}_source_date_mismatch_hidden", age)
                    counters[metric]["date"] += 1
                    continue
                source_ok = (
                    policy["active_source"](row)
                    if active
                    else policy["hold_source"](row)
                )
                if not source_ok:
                    _clear(row, policy, f"{metric}_source_untrusted_hidden", age)
                    counters[metric]["source"] += 1
                    continue
                if active:
                    max_age = float(policy["max_age"](row))
                    if age is None or age > max_age:
                        _clear(row, policy, f"{metric}_stale_hidden", age)
                        counters[metric]["stale"] += 1
                        continue
                    row[policy["basis_key"]] = policy["active_basis"]
                else:
                    row[policy["basis_key"]] = policy["hold_basis"]
                row[policy["available_key"]] = True
                row[policy["age_key"]] = None if age is None else round(age, 3)
                if str(row.get(policy["status_key"]) or "") in {
                    "",
                    "missing",
                    "unavailable",
                    "final_output_guard_hidden",
                }:
                    row[policy["status_key"]] = (
                        "current_session_valid" if active else "previous_session_final_hold"
                    )
                counters[metric]["visible"] += 1

        with self.lock:
            self.status["five_metric_display_policy_phase"] = phase
            self.status["five_metric_display_policy_mode"] = mode
            self.status["five_metric_display_policy_expected_date"] = expected or None
            for metric, values in counters.items():
                for reason, count in values.items():
                    self.status[f"five_metric_{metric}_{reason}_count"] = count
            self.status["five_metric_execution_transport_status"] = self.status.get(
                "realtime_strength_ws_status"
            )
            self.status["five_metric_execution_transport_error"] = self.status.get(
                "realtime_strength_ws_last_error"
            )
        return result

    state_class.__init__ = state_init
    state_class.rows = rows
    if callable(original_apply_orderbook):
        state_class._apply_orderbook = apply_orderbook
    state_class._stockboard_five_metric_display_policy_installed = True
