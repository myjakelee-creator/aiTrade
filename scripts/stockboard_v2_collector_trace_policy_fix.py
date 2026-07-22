from __future__ import annotations

"""Correct optional-status handling for the operator-only collector trace.

The production Collector and Worker do not import this module. The launcher uses this
wrapper so missing optional heartbeat fields remain UNKNOWN instead of being converted
to false/zero. Downstream counter activity is treated as stronger evidence than an
absent optional status field.
"""

from typing import Any

from scripts import stockboard_v2_collector_trace as base


def _optional_delta(
    first: dict[str, Any], last: dict[str, Any], key: str
) -> float | None:
    start = base._number(first.get(key))
    end = base._number(last.get(key))
    if start is None or end is None:
        return None
    return end - start


def _positive(value: float | None) -> bool:
    return value is not None and value > 0


def _nonpositive(value: float | None) -> bool:
    return value is not None and value <= 0


def _optional_unknown(first: dict[str, Any], last: dict[str, Any]) -> bool:
    optional_fields = (
        "provider_qt_pump_running",
        "provider_trade_event_applied_count",
        "provider_trade_last_received_at",
        "provider_trade_last_received_code",
        "provider_trade_last_fid10_raw",
        "provider_trade_last_fid20_raw",
    )
    return any(last.get(key) is None for key in optional_fields) or any(
        first.get(key) is None or last.get(key) is None
        for key in ("provider_trade_event_applied_count",)
    )


def _classify(first: dict[str, Any], last: dict[str, Any]) -> str:
    login_state = last.get("provider_login_state")
    if login_state is not None and login_state != "connected":
        return "QAX_LOGIN_NOT_CONNECTED"

    realreg = last.get("provider_realreg_succeeded")
    if realreg is False:
        return "SETREALREG_NOT_READY"

    qt_running = last.get("provider_qt_pump_running")
    if qt_running is False:
        return "QT_EVENT_PUMP_NOT_RUNNING"

    sender_connected = last.get("sender_connected")
    if sender_connected is False:
        return "EVENT_SENDER_NOT_CONNECTED"

    realdata_delta = _optional_delta(
        first, last, "provider_realdata_received_count"
    )
    trade_callback_delta = _optional_delta(
        first, last, "provider_trade_event_received_count"
    )
    applied_delta = _optional_delta(
        first, last, "provider_trade_event_applied_count"
    )
    sender_trade_delta = _optional_delta(
        first, last, "sender_received_trade_count"
    )
    worker_trade_delta = _optional_delta(first, last, "worker_trade_count")

    # Worker trade growth proves that QAx -> Provider -> Store -> Sender -> Worker was
    # active, even when optional heartbeat fields are absent from this runtime build.
    if _positive(worker_trade_delta):
        return (
            "PRICE_PATH_ACTIVE_WITH_OPTIONAL_STATUS_UNKNOWN"
            if _optional_unknown(first, last)
            else "PRICE_PATH_ACTIVE"
        )

    # Sender growth proves the path through Provider/store even if the optional
    # provider-applied counter is unavailable.
    if _positive(sender_trade_delta):
        if worker_trade_delta is None:
            return "EVENT_SENDER_ACTIVE_WORKER_STATUS_UNKNOWN"
        pending = base._number(last.get("sender_pending_trade_count")) or 0.0
        if pending > 0:
            return "EVENT_SENDER_PENDING_TRADE"
        return "EVENT_SENDER_OR_WORKER_GAP"

    if _positive(trade_callback_delta):
        if sender_trade_delta is None:
            return "PROVIDER_ACTIVE_SENDER_STATUS_UNKNOWN"
        if _nonpositive(sender_trade_delta):
            if _nonpositive(applied_delta):
                return "PROVIDER_TRADE_NOT_APPLIED"
            if applied_delta is None:
                return "PROVIDER_TRADE_APPLY_STATUS_UNKNOWN"
            return "STORE_TO_EVENT_SENDER_GAP"

    if _positive(realdata_delta):
        if trade_callback_delta is None:
            return "QAX_REALDATA_ACTIVE_TRADE_STATUS_UNKNOWN"
        return "QAX_REALDATA_WITHOUT_STOCK_TRADE"

    known_activity_counters = (
        realdata_delta,
        trade_callback_delta,
        sender_trade_delta,
        worker_trade_delta,
    )
    if all(value is None for value in known_activity_counters):
        return "INSUFFICIENT_STATUS_DATA"
    return "NO_QAX_REALDATA_CALLBACK"


# The legacy operator tool resolves these names from its module globals at runtime.
# Replacing them changes only this one-shot process; no production module imports it.
base._delta = _optional_delta
base._classify = _classify


if __name__ == "__main__":
    raise SystemExit(base.main())
