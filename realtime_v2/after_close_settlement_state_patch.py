from __future__ import annotations

"""Expose a calendar-based after-close settlement state without touching price flow.

This wrapper owns no QAx, FID, REST, WebSocket, thread, timer, or SSE cadence.
It only classifies the already-received last-good board after the configured
``aftermarket_end`` so operators can distinguish late-arrival grace,
provisional close, final hold, and restart recovery.
"""

from datetime import datetime, time as dt_time, timedelta
from typing import Any

PATCH_VERSION = "after_close_settlement_state_v2_function_marker"
QUIET_CONFIRM_SEC = 300
HARD_CUTOFF_MIN = 30
_CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}
_ROWS_MARKER = "_after_close_settlement_state_wrapper"
_TIMESTAMP_FIELDS = (
    "last_trade_event_received_at",
    "trade_received_at",
    "price_received_at",
    "received_at",
)


def _parse_clock(value: Any, fallback: str = "20:00") -> dt_time:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return dt_time(int(hour_text), int(minute_text[:2]))
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return dt_time(int(hour_text), int(minute_text))


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _latest_trade_received_at(state: Any) -> datetime | None:
    latest: datetime | None = None
    with state.lock:
        status = getattr(state, "status", {}) or {}
        candidates = [status.get(key) for key in _TIMESTAMP_FIELDS]
        for quote in (getattr(state, "quotes", {}) or {}).values():
            if not isinstance(quote, dict):
                continue
            candidates.extend(quote.get(key) for key in _TIMESTAMP_FIELDS)
    for raw in candidates:
        parsed = _parse_datetime(raw)
        if parsed is None:
            continue
        if latest is None or parsed.timestamp() > latest.timestamp():
            latest = parsed
    return latest


def settlement_state(state: Any, session: Any, now: datetime) -> dict[str, Any]:
    phase = str(getattr(session, "phase", "") or "")
    windows = getattr(session, "windows", {}) or {}
    trading_date = str(getattr(session, "trading_date", "") or "")
    latest = _latest_trade_received_at(state)

    if phase not in _CLOSED_PHASES:
        return {
            "state": "active_session",
            "target_trading_date": trading_date or None,
            "latest_trade_received_at": latest.isoformat() if latest else None,
            "quiet_sec": None,
            "hard_cutoff_at": None,
        }

    if phase in {"weekend", "holiday", "before_market"}:
        return {
            "state": "final_close_hold",
            "target_trading_date": trading_date or None,
            "latest_trade_received_at": latest.isoformat() if latest else None,
            "quiet_sec": None,
            "hard_cutoff_at": None,
        }

    aftermarket_end = _parse_clock(windows.get("aftermarket_end"), "20:00")
    close_at = datetime.combine(now.date(), aftermarket_end)
    hard_cutoff_at = close_at + timedelta(minutes=HARD_CUTOFF_MIN)
    quiet_sec = None
    latest_for_compare = latest
    if latest is not None:
        if latest.tzinfo is not None and now.tzinfo is None:
            latest_for_compare = latest.replace(tzinfo=None)
        elif latest.tzinfo is None and now.tzinfo is not None:
            latest_for_compare = latest.replace(tzinfo=now.tzinfo)
        quiet_sec = max(0.0, (now - latest_for_compare).total_seconds())

    if now < hard_cutoff_at:
        if latest is not None and (
            latest_for_compare >= close_at or (quiet_sec or 0.0) < QUIET_CONFIRM_SEC
        ):
            current_state = "late_arrival_grace"
        elif latest is None:
            current_state = "restart_recovery_wait"
        else:
            current_state = "provisional_close"
    else:
        current_state = "final_close_hold"

    return {
        "state": current_state,
        "target_trading_date": trading_date or None,
        "latest_trade_received_at": latest.isoformat() if latest else None,
        "quiet_sec": round(quiet_sec, 3) if quiet_sec is not None else None,
        "hard_cutoff_at": hard_cutoff_at.isoformat(),
    }


def install(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None:
        return

    current_rows = state_class.rows
    if getattr(current_rows, _ROWS_MARKER, False):
        return

    from realtime_v2.market_session import market_session_now

    # Class-level markers can be stale after worker64_guarded.py replaces rows.
    # The live function marker is authoritative.
    original_rows = current_rows

    def rows(self, *args, **kwargs):
        now = datetime.now().astimezone()
        session = market_session_now(now)
        info = settlement_state(self, session, now)
        with self.lock:
            self.status.update(
                {
                    "after_close_settlement_version": PATCH_VERSION,
                    "after_close_settlement_state": info["state"],
                    "after_close_settlement_target_trading_date": info["target_trading_date"],
                    "after_close_settlement_latest_trade_received_at": info["latest_trade_received_at"],
                    "after_close_settlement_quiet_sec": info["quiet_sec"],
                    "after_close_settlement_hard_cutoff_at": info["hard_cutoff_at"],
                    "after_close_late_event_policy": "accept_and_checkpoint_last_good",
                    "after_close_restart_policy": "checkpoint_then_exact_once_if_missing",
                }
            )
        return original_rows(self, *args, **kwargs)

    setattr(rows, _ROWS_MARKER, True)
    setattr(rows, "_after_close_settlement_state_version", PATCH_VERSION)
    state_class.rows = rows
    state_class._after_close_settlement_state_installed = True
    state_class._after_close_settlement_state_version = PATCH_VERSION


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_after_close_settlement_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        install(base)

    opening_module.install = install_after_opening
    opening_module._after_close_settlement_install_wrapped = True
