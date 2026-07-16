from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "minute_value_hold_v1"
ACTIVE_MINUTE_VALUE_PHASES = {
    "opening_call",
    "opening_burst",
    "regular",
    "closing_call",
    "after_wait",
    "aftermarket",
}
MINUTE_VALUE_KEYS = (
    "trade_value_1m_eok",
    "trade_value_prev_1m_eok",
    "trade_value_1m_ratio_pct",
    "trade_value_1m_quality",
    "trade_value_1m_observed_at",
    "trade_value_1m_source_trading_date",
    "trade_value_1m_source",
)


def _phase_name() -> str:
    try:
        return str(getattr(market_session_now(datetime.now()), "phase", "") or "").lower()
    except Exception:
        return "unknown"


def _completed_minute() -> int:
    from realtime_v2.worker_approved_minute_pipeline import _minute_number

    return int(_minute_number()) - 1


def minute_value_should_hold(
    phase: str,
    buckets: dict[int, Any] | None,
    completed_minute: int,
) -> bool:
    """Fail closed unless an active session has a positive completed-minute bucket."""

    if str(phase or "").lower() not in ACTIVE_MINUTE_VALUE_PHASES:
        return True
    if not isinstance(buckets, dict) or completed_minute not in buckets:
        return True
    value = to_number(buckets.get(completed_minute))
    return value is None or float(value) <= 0


def _snapshot(mapping: dict[str, Any] | None) -> dict[str, tuple[bool, Any]]:
    mapping = mapping if isinstance(mapping, dict) else {}
    return {
        key: (key in mapping, deepcopy(mapping.get(key)))
        for key in MINUTE_VALUE_KEYS
    }


def _restore(mapping: dict[str, Any] | None, snapshot: dict[str, tuple[bool, Any]]) -> None:
    if not isinstance(mapping, dict):
        return
    for key, (present, value) in snapshot.items():
        if present:
            mapping[key] = deepcopy(value)
        else:
            mapping.pop(key, None)


def _restore_row(
    row: dict[str, Any],
    quote_snapshot: dict[str, tuple[bool, Any]],
    daily_snapshot: dict[str, tuple[bool, Any]],
) -> None:
    for key in MINUTE_VALUE_KEYS:
        quote_present, quote_value = quote_snapshot[key]
        daily_present, daily_value = daily_snapshot[key]
        if quote_present:
            row[key] = deepcopy(quote_value)
        elif daily_present:
            row[key] = deepcopy(daily_value)
        else:
            row.pop(key, None)


def install(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_minute_value_hold_installed",
        False,
    ):
        return

    original_init = state_class.__init__
    original_rows = state_class.rows
    original_publish = getattr(state_class, "publish_approved_minute_metrics", None)

    def state_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        with self.lock:
            self.status.update(
                {
                    "minute_value_hold_installed": True,
                    "minute_value_hold_version": PATCH_VERSION,
                    "minute_value_hold_policy": "active_positive_completed_bucket_only",
                }
            )

    def protected_snapshots(self):
        phase = _phase_name()
        completed = _completed_minute()
        protected: dict[
            str,
            tuple[
                dict[str, tuple[bool, Any]],
                dict[str, tuple[bool, Any]],
            ],
        ] = {}
        with self.lock:
            codes = (
                set(getattr(self, "quotes", {}))
                | set(getattr(self, "daily_values_by_code", {}))
                | set(getattr(self, "_approved_trade_value_buckets", {}))
            )
            for raw_code in codes:
                code = normalize_code(raw_code)
                if not code:
                    continue
                buckets = getattr(self, "_approved_trade_value_buckets", {}).get(code)
                if not minute_value_should_hold(phase, buckets, completed):
                    continue
                protected[code] = (
                    _snapshot(getattr(self, "quotes", {}).get(code)),
                    _snapshot(getattr(self, "daily_values_by_code", {}).get(code)),
                )
        return phase, completed, protected

    def restore_internal(self, protected) -> None:
        with self.lock:
            for code, (quote_snapshot, daily_snapshot) in protected.items():
                _restore(getattr(self, "quotes", {}).get(code), quote_snapshot)
                daily = getattr(self, "daily_values_by_code", {}).setdefault(code, {})
                _restore(daily, daily_snapshot)

    def update_status(self, phase: str, completed: int, count: int) -> None:
        with self.lock:
            self.status["minute_value_hold_phase"] = phase
            self.status["minute_value_hold_completed_minute"] = completed
            self.status["minute_value_hold_count"] = count
            self.status["minute_value_hold_last_at"] = now_text()

    def rows(self, limit: int = 300):
        phase, completed, protected = protected_snapshots(self)
        result = original_rows(self, limit)
        restore_internal(self, protected)
        if isinstance(result, list):
            for row in result:
                if not isinstance(row, dict):
                    continue
                code = normalize_code(row.get("stock_code"))
                snapshots = protected.get(code)
                if snapshots is not None:
                    _restore_row(row, snapshots[0], snapshots[1])
        update_status(self, phase, completed, len(protected))
        return result

    def publish(self, force: bool = False):
        phase, completed, protected = protected_snapshots(self)
        result = original_publish(self, force) if callable(original_publish) else False
        restore_internal(self, protected)
        update_status(self, phase, completed, len(protected))
        return result

    state_class.__init__ = state_init
    state_class.rows = rows
    if callable(original_publish):
        state_class.publish_approved_minute_metrics = publish
    state_class._stockboard_minute_value_hold_installed = True
