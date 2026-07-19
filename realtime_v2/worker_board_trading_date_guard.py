from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.common import RUNTIME_DIR, normalize_code, now_text, to_number
from realtime_v2.market_session import last_completed_trading_date, market_session_now

PATCH_VERSION = "portable_board_trading_date_guard_v1"
PORTABLE_POLICY_VERSION = "portable_closed_board_snapshot_v1"
SNAPSHOT_PATH = RUNTIME_DIR / "ohlc_snapshot.json"
ACTIVE_PHASES = {
    "premarket",
    "opening_call",
    "regular",
    "closing_call",
    "after_wait",
    "aftermarket",
}


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def board_target_context(now: datetime | None = None) -> tuple[str, str, bool]:
    current = now or datetime.now()
    session = market_session_now(current)
    phase = str(session.phase or "")
    active = bool(session.is_trading_day and phase in ACTIVE_PHASES)
    if active:
        target_date = str(session.trading_date or "")
    else:
        target_date = str(last_completed_trading_date(current) or "")
    if not target_date:
        target_date = str(session.trading_date or session.calendar_date or "")
    return _date_digits(target_date), phase, active


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _positive(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    result = float(number)
    return result if result > 0 else None


class PortableBoardGuard:
    def __init__(self, snapshot_path: Path = SNAPSHOT_PATH) -> None:
        self.snapshot_path = Path(snapshot_path)
        self._last_check = 0.0
        self._mtime: float | None = None
        self._payload: dict[str, Any] | None = None

    def _load(self, force: bool = False) -> dict[str, Any] | None:
        now_mono = time.monotonic()
        if not force and now_mono - self._last_check < 2.0:
            return self._payload
        self._last_check = now_mono
        try:
            mtime = self.snapshot_path.stat().st_mtime
        except OSError:
            self._payload = None
            self._mtime = None
            return None
        if not force and self._payload is not None and self._mtime == mtime:
            return self._payload
        self._mtime = mtime
        self._payload = _read_json(self.snapshot_path)
        return self._payload

    @staticmethod
    def _status(
        state,
        *,
        target_date: str,
        phase: str,
        basis: str,
        payload: dict[str, Any] | None,
        exact_count: int = 0,
        missing_count: int = 0,
    ) -> None:
        payload = payload if isinstance(payload, dict) else {}
        state.status.update(
            {
                "board_trading_date_guard_version": PATCH_VERSION,
                "board_expected_trading_date": target_date or None,
                "board_market_phase": phase,
                "board_display_basis": basis,
                "board_source_trading_date": _date_digits(
                    payload.get("source_trading_date") or payload.get("trading_date")
                )
                or None,
                "board_market_scope": payload.get("market_scope"),
                "board_portable_verified": bool(payload.get("verified")),
                "board_portable_coverage": payload.get("coverage"),
                "board_exact_row_count": exact_count,
                "board_missing_row_count": missing_count,
                "board_snapshot_path": str(SNAPSHOT_PATH),
                "board_snapshot_updated_at": payload.get("ts"),
            }
        )

    def apply(self, state, now: datetime | None = None) -> bool:
        target_date, phase, active = board_target_context(now)
        if active:
            self._status(
                state,
                target_date=target_date,
                phase=phase,
                basis="live_session_passthrough",
                payload=None,
            )
            return True

        payload = self._load()
        source_date = _date_digits(
            payload.get("source_trading_date") or payload.get("trading_date")
            if isinstance(payload, dict)
            else None
        )
        board_values = payload.get("board_values") if isinstance(payload, dict) else None
        valid = bool(
            isinstance(payload, dict)
            and payload.get("portable_policy_version") == PORTABLE_POLICY_VERSION
            and payload.get("verified") is True
            and source_date
            and source_date == target_date
            and isinstance(board_values, dict)
        )
        if not valid:
            self._status(
                state,
                target_date=target_date,
                phase=phase,
                basis="blocked_waiting_exact_portable_snapshot",
                payload=payload,
                exact_count=0,
                missing_count=len(getattr(state, "seed_rank_by_code", {}) or {}),
            )
            return False

        exact_by_code: dict[str, dict[str, Any]] = {}
        for raw_code, raw_value in board_values.items():
            code = normalize_code(raw_code)
            if code and isinstance(raw_value, dict):
                exact_by_code[code] = raw_value

        previous_ranked = sorted(
            (
                (code, _positive(value.get("prev_trade_value_eok")))
                for code, value in exact_by_code.items()
            ),
            key=lambda item: (-(item[1] or 0.0), item[0]),
        )
        previous_rank_by_code = {
            code: rank
            for rank, (code, value) in enumerate(previous_ranked, start=1)
            if value is not None
        }

        exact_count = 0
        missing_count = 0
        with state.lock:
            universe_codes = list(getattr(state, "seed_rank_by_code", {}) or {})
            for code in universe_codes:
                exact = exact_by_code.get(code)
                quote = state._quote(code)
                if not isinstance(exact, dict):
                    for key in (
                        "price",
                        "trade_price",
                        "change_rate",
                        "trade_value_eok",
                        "amount_ratio",
                        "ohlc",
                        "day_open",
                        "day_high",
                        "day_low",
                        "day_close",
                    ):
                        quote.pop(key, None)
                    quote["portable_board_missing"] = True
                    missing_count += 1
                    continue

                price = _positive(exact.get("price"))
                trade_value = to_number(exact.get("trade_value_eok"))
                change_rate = to_number(exact.get("change_rate"))
                ohlc = exact.get("ohlc") if isinstance(exact.get("ohlc"), dict) else None
                if price is None or trade_value is None or change_rate is None or ohlc is None:
                    quote["portable_board_missing"] = True
                    missing_count += 1
                    continue

                quote.update(
                    {
                        "price": price,
                        "trade_price": price,
                        "change_rate": round(float(change_rate), 4),
                        "trade_value_eok": round(float(trade_value), 4),
                        "ohlc": deepcopy(ohlc),
                        "day_open": ohlc.get("open"),
                        "day_high": ohlc.get("high"),
                        "day_low": ohlc.get("low"),
                        "day_close": ohlc.get("close"),
                        "prev_trade_value_eok": exact.get("prev_trade_value_eok"),
                        "prev_trade_value_date": exact.get("prev_trade_value_date"),
                        "prev_rank": previous_rank_by_code.get(code),
                        "source_code": "portable_exact_close",
                        "row_source": "portable_exact_close",
                        "source_trading_date": target_date,
                        "price_trading_date": target_date,
                        "change_rate_trading_date": target_date,
                        "trade_value_trading_date": target_date,
                        "ohlc_trading_date": target_date,
                        "market_scope": exact.get("market_scope"),
                        "portable_board_quality": exact.get("quality"),
                        "portable_board_applied_at": now_text(),
                        "price_age_sec": None,
                    }
                )
                quote.pop("received_at", None)
                quote.pop("portable_board_missing", None)
                state.prev_trade_value_by_code[code] = float(
                    exact.get("prev_trade_value_eok") or 0.0
                )
                if previous_rank_by_code.get(code):
                    state.prev_rank_by_code[code] = previous_rank_by_code[code]
                exact_count += 1

        self._status(
            state,
            target_date=target_date,
            phase=phase,
            basis="portable_exact_close",
            payload=payload,
            exact_count=exact_count,
            missing_count=missing_count,
        )
        return exact_count > 0


def install(base) -> None:
    """Install cross-PC closed-session row protection.

    No QAx, FID, REST request, WebSocket, worker thread, timer, or SSE cadence is
    added.  The guard only consumes the existing low-priority context snapshot.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_portable_board_guard_installed", False
    ):
        return

    original_rows = state_class.rows

    def rows(self, limit: int = 300):
        guard = getattr(self, "portable_board_guard", None)
        if guard is None:
            guard = PortableBoardGuard()
            self.portable_board_guard = guard
        if not guard.apply(self):
            return []
        return original_rows(self, limit)

    state_class.rows = rows
    state_class._stockboard_portable_board_guard_installed = True
