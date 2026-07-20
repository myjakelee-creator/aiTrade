from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code
from realtime_v2.market_session import last_completed_trading_date, market_session_now

PATCH_VERSION = "metric_source_trading_date_v1"
CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _target_date() -> str:
    now = datetime.now()
    session = market_session_now(now)
    phase = str(session.phase or "")
    if phase in CLOSED_PHASES:
        return _date_digits(last_completed_trading_date(now))
    return _date_digits(session.trading_date or session.calendar_date)


def _extend_lifecycle_group_dates() -> None:
    import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

    additions = {
        "amount_ratio": "amount_ratio_source_trading_date",
        "orderbook": "orderbook_source_trading_date",
        "execution": "execution_source_trading_date",
        "strength5": "strength_source_trading_date",
        "program": "program_source_trading_date",
        "large_trade": "large_trade_source_trading_date",
    }
    for group, key in additions.items():
        config = lifecycle.GROUPS.get(group)
        if not isinstance(config, dict):
            continue
        config["capture_keys"] = tuple(dict.fromkeys((key, *config.get("capture_keys", ()))))
        config["date_keys"] = tuple(dict.fromkeys((key, *config.get("date_keys", ()))))
    lifecycle.PERSIST_KEYS = tuple(
        dict.fromkeys(
            key
            for config in lifecycle.GROUPS.values()
            for key in config.get("capture_keys", ())
        )
    )


def install(base) -> None:
    """Stamp metrics with their actual source trading date.

    Poll timestamps can fall on the following calendar day while the returned values
    still belong to the last completed session. This patch keeps poll time and source
    trading date separate so the lifecycle layer does not hide correct close data or
    accept old cache values accidentally.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(state_class, "_stockboard_metric_provenance_installed", False):
        return

    _extend_lifecycle_group_dates()
    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys(
            (
                *getattr(base, "DAILY_PERSIST_KEYS", ()),
                "amount_ratio_source_trading_date",
                "orderbook_source_trading_date",
                "execution_source_trading_date",
                "strength_source_trading_date",
                "program_source_trading_date",
                "large_trade_source_trading_date",
            )
        )
    )

    original_state_init = state_class.__init__
    original_program = state_class.apply_program_net_values
    original_rest = getattr(state_class, "apply_rest_live_metric_values", None)
    original_execution = getattr(state_class, "apply_realtime_strength_ws", None)

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["metric_provenance_installed"] = True
            self.status["metric_provenance_version"] = PATCH_VERSION

    def apply_program_net_values(self, values, source, status):
        updated = original_program(self, values, source, status)
        source_date = _target_date()
        if not source_date:
            return updated
        with self.lock:
            for raw_code in (values or {}):
                code = normalize_code(raw_code)
                if not code:
                    continue
                daily = self.daily_values_by_code.get(code)
                if not isinstance(daily, dict) or "program_net" not in daily:
                    continue
                daily["program_source_trading_date"] = source_date
                daily["_session_hold_program_date"] = source_date
                quote = self.quotes.get(code)
                if isinstance(quote, dict):
                    quote["program_source_trading_date"] = source_date
                    quote["_session_hold_program_date"] = source_date
            if updated:
                self.status["program_source_trading_date"] = source_date
                self._mark_daily_dirty()
        return updated

    def apply_rest_live_metric_values(self, raw_code, values, metric):
        updated = original_rest(self, raw_code, values, metric)
        if not updated:
            return updated
        source_date = _target_date()
        code = normalize_code(raw_code)
        if not code or not source_date:
            return updated
        if metric == "bidask":
            date_key = "orderbook_source_trading_date"
            marker = "_session_hold_orderbook_date"
            source_key, status_key = "orderbook_source", "orderbook_status"
            source_default = "ka10004_rest_lowload"
        elif metric == "strength":
            date_key = "strength_source_trading_date"
            marker = "_session_hold_strength5_date"
            source_key, status_key = "strength_source", "strength_status"
            source_default = "ka10046_rest_lowload"
        else:
            return updated
        with self.lock:
            daily = self.daily_values_by_code.setdefault(code, {})
            quote = self.quotes.get(code)
            for target in (daily, quote):
                if not isinstance(target, dict):
                    continue
                target[date_key] = source_date
                target[marker] = source_date
                if not target.get(source_key):
                    target[source_key] = source_default
                if not target.get(status_key):
                    target[status_key] = "ok"
            self.status[f"{metric}_source_trading_date"] = source_date
            self._mark_daily_dirty()
        return updated

    def apply_realtime_strength_ws(self, event):
        changed = original_execution(self, event)
        code = normalize_code(event.get("stock_code")) if isinstance(event, dict) else ""
        source_date = _target_date()
        if not code or not source_date:
            return changed
        with self.lock:
            daily = self.daily_values_by_code.setdefault(code, {})
            quote = self.quotes.get(code)
            for target in (daily, quote):
                if isinstance(target, dict):
                    target["execution_source_trading_date"] = source_date
                    target["_session_hold_execution_date"] = source_date
            self.status["execution_source_trading_date"] = source_date
            self._mark_daily_dirty()
        return changed

    state_class.__init__ = state_init
    state_class.apply_program_net_values = apply_program_net_values
    if callable(original_rest):
        state_class.apply_rest_live_metric_values = apply_rest_live_metric_values
    if callable(original_execution):
        state_class.apply_realtime_strength_ws = apply_realtime_strength_ws
    state_class._stockboard_metric_provenance_installed = True
