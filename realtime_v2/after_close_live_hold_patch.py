from __future__ import annotations

"""Keep and persist the final live board across the calendar-defined close.

This patch is outside the collector/price callback path.  It adds no QAx, FID,
REST, WebSocket, worker thread, timer, or SSE cadence.  The already requested
board rows are used to checkpoint the last valid board only after the regular
close window begins.

Policy:
- accepted current-day worker values remain authoritative after close;
- late events after the configured aftermarket end keep updating State and the
  next low-frequency board request refreshes the checkpoint;
- restart/new connection restores the local last-good checkpoint first;
- the existing verified portable exact-close path is used only when no matching
  local checkpoint and no accepted in-memory values exist;
- a verified newer-day quote is never overwritten by the previous close hold.
"""

import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

PATCH_VERSION = "after_close_live_hold_v3_function_marker"
CHECKPOINT_VERSION = "after_close_live_checkpoint_v1"
CHECKPOINT_FILENAME = "after_close_live_checkpoint.json"
CHECKPOINT_INTERVAL_SEC = 10.0
_SAVE_PHASES = {"after_wait", "aftermarket", "closed"}
_RESTORE_PHASES = {
    "before_market",
    "premarket",
    "opening_call",
    "closed",
    "weekend",
    "holiday",
}
_DATE_FIELDS = (
    "received_at",
    "price_received_at",
    "trade_received_at",
    "last_trade_event_received_at",
    "source_trading_date",
    "price_trading_date",
    "trade_value_trading_date",
)
_APPLY_MARKER = "_stockboard_after_close_live_hold_apply_wrapper"
_ROWS_MARKER = "_stockboard_after_close_live_hold_rows_wrapper"


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _runtime_dir(base) -> Path:
    candidate = getattr(base, "RUNTIME_DIR", None)
    if candidate:
        return Path(candidate)
    return Path(__file__).resolve().parents[1] / "data" / "runtime" / "stockboard_v2"


def _checkpoint_path(base) -> Path:
    return _runtime_dir(base) / CHECKPOINT_FILENAME


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_checkpoint(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _valid_quote_for_date(quote: Any, target_date: str) -> bool:
    if not isinstance(quote, dict) or not target_date:
        return False
    price = _number(quote.get("price") or quote.get("trade_price"))
    trade_value = _number(quote.get("trade_value_eok"))
    if price is None or price <= 0 or trade_value is None or trade_value < 0:
        return False
    return any(_date_digits(quote.get(key)) == target_date for key in _DATE_FIELDS)


def _quote_has_newer_date(quote: Any, target_date: str) -> bool:
    if not isinstance(quote, dict) or not target_date:
        return False
    dates = [_date_digits(quote.get(key)) for key in _DATE_FIELDS]
    return any(value and value > target_date for value in dates)


def _current_day_live_count(state, target_date: str) -> int:
    if not target_date:
        return 0
    with state.lock:
        quotes = getattr(state, "quotes", {}) or {}
        return sum(
            1 for quote in quotes.values() if _valid_quote_for_date(quote, target_date)
        )


def _has_accepted_current_day_trades(state, target_date: str) -> tuple[bool, int]:
    with state.lock:
        trade_count = int(getattr(state, "status", {}).get("trade_count") or 0)
    live_count = _current_day_live_count(state, target_date)
    return trade_count > 0 and live_count > 0, live_count


def _checkpoint_rows(state, target_date: str) -> dict[str, dict[str, Any]]:
    with state.lock:
        quotes = getattr(state, "quotes", {}) or {}
        return {
            str(code): deepcopy(quote)
            for code, quote in quotes.items()
            if _valid_quote_for_date(quote, target_date)
        }


def _save_checkpoint_if_due(base, state, target_date: str, phase: str) -> bool:
    if phase not in _SAVE_PHASES or not target_date:
        return False
    now_mono = time.monotonic()
    last_saved_mono = float(
        getattr(state, "_after_close_checkpoint_last_saved_mono", 0.0) or 0.0
    )
    with state.lock:
        trade_count = int(getattr(state, "status", {}).get("trade_count") or 0)
    last_trade_count = int(
        getattr(state, "_after_close_checkpoint_trade_count", -1) or -1
    )
    if (
        now_mono - last_saved_mono < CHECKPOINT_INTERVAL_SEC
        or trade_count == last_trade_count
    ):
        return False

    rows = _checkpoint_rows(state, target_date)
    if not rows:
        return False
    payload = {
        "schema_version": 1,
        "checkpoint_version": CHECKPOINT_VERSION,
        "trading_date": target_date,
        "saved_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "phase": phase,
        "trade_count": trade_count,
        "row_count": len(rows),
        "rows": rows,
    }
    try:
        _atomic_write(_checkpoint_path(base), payload)
    except OSError as error:
        with state.lock:
            state.status["after_close_checkpoint_last_error"] = str(error)
        return False

    state._after_close_checkpoint_last_saved_mono = now_mono
    state._after_close_checkpoint_trade_count = trade_count
    with state.lock:
        state.status.update(
            {
                "after_close_checkpoint_version": CHECKPOINT_VERSION,
                "after_close_checkpoint_trading_date": target_date,
                "after_close_checkpoint_row_count": len(rows),
                "after_close_checkpoint_saved_at": payload["saved_at"],
                "after_close_checkpoint_last_error": None,
            }
        )
    return True


def _restore_checkpoint(base, state, target_date: str, phase: str) -> int:
    if phase not in _RESTORE_PHASES or not target_date:
        return 0
    if _current_day_live_count(state, target_date) > 0:
        return 0
    payload = _read_checkpoint(_checkpoint_path(base))
    if not isinstance(payload, dict):
        return 0
    if payload.get("checkpoint_version") != CHECKPOINT_VERSION:
        return 0
    if _date_digits(payload.get("trading_date")) != target_date:
        return 0
    rows = payload.get("rows")
    if not isinstance(rows, dict) or not rows:
        return 0

    restored = 0
    skipped_newer = 0
    with state.lock:
        for raw_code, raw_quote in rows.items():
            code = str(raw_code or "")
            if not code or not isinstance(raw_quote, dict):
                continue
            if not _valid_quote_for_date(raw_quote, target_date):
                continue
            existing = getattr(state, "quotes", {}).get(code)
            if _quote_has_newer_date(existing, target_date):
                skipped_newer += 1
                continue
            quote = state._quote(code)
            quote.clear()
            quote.update(deepcopy(raw_quote))
            quote["row_source"] = "after_close_live_checkpoint"
            quote["after_close_checkpoint_restored"] = True
            restored += 1
        if restored or skipped_newer:
            state.status.update(
                {
                    "after_close_checkpoint_version": CHECKPOINT_VERSION,
                    "after_close_checkpoint_restored": bool(restored),
                    "after_close_checkpoint_trading_date": target_date,
                    "after_close_checkpoint_row_count": restored,
                    "after_close_checkpoint_newer_day_skipped_count": skipped_newer,
                    "after_close_checkpoint_saved_at": payload.get("saved_at"),
                    "board_display_basis": "after_close_live_checkpoint",
                }
            )
    return restored


def install(base) -> None:
    from realtime_v2 import worker_board_trading_date_guard as guard_module
    from realtime_v2.market_session import last_completed_trading_date, market_session_now

    guard_class = guard_module.PortableBoardGuard
    current_apply = guard_class.apply
    if not getattr(current_apply, _APPLY_MARKER, False):
        original_apply = current_apply

        def apply(self, state, now: datetime | None = None):
            current = now or datetime.now()
            target_date, phase, active = guard_module.board_target_context(current)
            if not active:
                _restore_checkpoint(base, state, target_date, phase)
                live_ready, live_count = _has_accepted_current_day_trades(state, target_date)
                checkpoint_count = _current_day_live_count(state, target_date)
                if live_ready or checkpoint_count > 0:
                    with state.lock:
                        state.status.update(
                            {
                                "after_close_live_hold_version": PATCH_VERSION,
                                "after_close_live_hold_active": True,
                                "after_close_live_hold_target_date": target_date or None,
                                "after_close_live_hold_phase": phase,
                                "after_close_live_hold_row_count": max(live_count, checkpoint_count),
                                "after_close_live_hold_basis": (
                                    "accepted_worker_trades"
                                    if live_ready
                                    else "local_checkpoint"
                                ),
                                "board_display_basis": (
                                    "in_memory_after_close_live_hold"
                                    if live_ready
                                    else "after_close_live_checkpoint"
                                ),
                                "board_expected_trading_date": target_date or None,
                                "board_market_phase": phase,
                            }
                        )
                    self._applied_result = True
                    return True

            result = original_apply(self, state, current)
            with state.lock:
                state.status.update(
                    {
                        "after_close_live_hold_version": PATCH_VERSION,
                        "after_close_live_hold_active": False,
                        "after_close_live_hold_target_date": target_date or None,
                        "after_close_live_hold_phase": phase,
                        "after_close_live_hold_row_count": 0,
                        "after_close_live_hold_basis": (
                            "active_session" if active else "portable_exact_fallback"
                        ),
                    }
                )
            return result

        setattr(apply, _APPLY_MARKER, True)
        setattr(apply, "_stockboard_after_close_live_hold_version", PATCH_VERSION)
        guard_class.apply = apply

    guard_class._stockboard_after_close_live_hold_installed = True
    guard_class._stockboard_after_close_live_hold_version = PATCH_VERSION

    state_class = getattr(base, "State", None)
    if state_class is not None:
        current_rows = state_class.rows
        if not getattr(current_rows, _ROWS_MARKER, False):
            original_rows = current_rows

            def rows(self, *args, **kwargs):
                session = market_session_now()
                completed_date = str(last_completed_trading_date() or "")
                target_date = (
                    str(session.trading_date or "")
                    if session.phase in _SAVE_PHASES
                    else completed_date
                )
                if session.phase in _RESTORE_PHASES:
                    _restore_checkpoint(base, self, target_date, session.phase)
                if session.phase in _SAVE_PHASES:
                    _save_checkpoint_if_due(
                        base,
                        self,
                        str(session.trading_date or target_date),
                        session.phase,
                    )
                result = original_rows(self, *args, **kwargs)
                status = getattr(self, "status", None)
                lock = getattr(self, "lock", None)
                values = {
                    "after_close_live_hold_rows_wrapper_installed": True,
                    "after_close_live_hold_rows_wrapper_version": PATCH_VERSION,
                    "after_close_live_hold_rows_wrapper_phase": str(session.phase or ""),
                }
                if lock is not None and isinstance(status, dict):
                    with lock:
                        status.update(values)
                elif isinstance(status, dict):
                    status.update(values)
                return result

            setattr(rows, _ROWS_MARKER, True)
            setattr(rows, "_stockboard_after_close_live_hold_version", PATCH_VERSION)
            state_class.rows = rows

        state_class._stockboard_after_close_live_hold_installed = True
        state_class._stockboard_after_close_live_hold_version = PATCH_VERSION


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_after_close_live_hold_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        install(base)

    opening_module.install = install_after_opening
    opening_module._after_close_live_hold_install_wrapped = True
