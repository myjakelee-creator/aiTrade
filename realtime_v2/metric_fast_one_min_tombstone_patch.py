from __future__ import annotations

"""Emit explicit one-minute tombstones on the closed-session metric stream.

The full snapshot removes unproven zero values, but the browser merges metric
SSE rows with Object.assign(). Omitting a key therefore cannot remove a zero
that was already present. This output-only wrapper sends explicit nulls for all
known one-minute trade-value aliases when there is no completed-bucket proof.
"""

from typing import Any

from realtime_v2.market_session import market_session_now

PATCH_VERSION = "metric_fast_one_min_tombstone_v1"
CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}
_MARKER = "_stockboard_metric_fast_one_min_tombstone_wrapper"
_ALIASES = (
    "trade_value_1m_eok",
    "minute_trade_value_eok",
    "one_min_trade_value_eok",
)
_PROVEN_STATUSES = {
    "ok",
    "new",
    "complete",
    "completed",
    "exact",
    "exact_live",
    "same_session_last_valid",
}


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _clear_unproven_minute(row: dict[str, Any]) -> bool:
    status = str(
        row.get("one_min_status")
        or row.get("minute_trade_value_status")
        or ""
    ).strip().lower()
    if status in _PROVEN_STATUSES:
        return False

    values = [_number(row.get(key)) for key in _ALIASES]
    if any(value is not None and value > 0 for value in values):
        return False

    for key in _ALIASES:
        row[key] = None
    row["one_min_available"] = False
    row["one_min_status"] = "unavailable_without_completed_bucket"
    row["minute_trade_value_status"] = row["one_min_status"]
    row["one_min_tombstone"] = True
    return True


def install(base) -> None:
    state_class = getattr(base, "State", None)
    current = getattr(state_class, "metric_fast_snapshot", None) if state_class else None
    if not callable(current) or getattr(current, _MARKER, False):
        return

    original = current

    def metric_fast_snapshot(self, *args, **kwargs):
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        phase = str(getattr(market_session_now(), "phase", "") or "")
        if phase not in CLOSED_PHASES:
            return payload
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return payload

        cleared = 0
        for row in rows:
            if isinstance(row, dict):
                cleared += int(_clear_unproven_minute(row))

        payload["metric_fast_one_min_tombstone_version"] = PATCH_VERSION
        payload["metric_fast_one_min_tombstone_phase"] = phase
        payload["metric_fast_one_min_tombstone_count"] = cleared
        status = getattr(self, "status", None)
        lock = getattr(self, "lock", None)
        values = {
            "metric_fast_one_min_tombstone_version": PATCH_VERSION,
            "metric_fast_one_min_tombstone_phase": phase,
            "metric_fast_one_min_tombstone_count": cleared,
        }
        if lock is not None and isinstance(status, dict):
            with lock:
                status.update(values)
        elif isinstance(status, dict):
            status.update(values)
        return payload

    setattr(metric_fast_snapshot, _MARKER, True)
    state_class.metric_fast_snapshot = metric_fast_snapshot
    state_class._stockboard_metric_fast_one_min_tombstone_installed = True


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_metric_fast_one_min_tombstone_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        install(base)

    opening_module.install = install_after_opening
    opening_module._metric_fast_one_min_tombstone_install_wrapped = True
