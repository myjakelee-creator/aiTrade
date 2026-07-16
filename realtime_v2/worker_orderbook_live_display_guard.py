from __future__ import annotations

from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "orderbook_live_display_guard_v1"
ACTIVE_PHASES = {
    "premarket",
    "opening_call",
    "regular",
    "closing_call",
    "after_wait",
    "aftermarket",
}
MAX_LIVE_AGE_SEC = 3.0
ORDERBOOK_DISPLAY_KEYS = (
    "bid_ask_ratio",
    "bid_pct",
    "ask_pct",
    "bid_volume",
    "ask_volume",
    "best_ask_price",
    "best_bid_price",
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


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


def _is_live_source(row: dict[str, Any]) -> bool:
    if row.get("orderbook_live") is True:
        return True
    source = str(row.get("orderbook_source") or "").strip().lower()
    if not source or "rest_lowload" in source:
        return False
    return any(
        token in source
        for token in (
            "realtime_orderbook",
            "websocket_orderbook",
            "ws_orderbook",
            "qax_orderbook",
        )
    )


def _hide(row: dict[str, Any], status: str, age: float | None) -> None:
    for key in ORDERBOOK_DISPLAY_KEYS:
        row.pop(key, None)
    row["orderbook_available"] = False
    row["orderbook_status"] = status
    row["orderbook_display_basis"] = "hidden_until_live_orderbook"
    row["orderbook_age_sec"] = None if age is None else round(age, 3)


def install(base) -> None:
    """Show bid/ask ratio during active sessions only from fresh live orderbook data.

    Sequential ka10004 REST snapshots are useful for diagnostics and close-state recovery,
    but they are not authoritative realtime orderbook values. During active trading phases
    this final output wrapper hides any non-live source and any live value older than three
    seconds. Closed/before-market/weekend/holiday lifecycle holds remain unchanged.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_orderbook_live_display_guard_installed",
        False,
    ):
        return

    original_state_init = state_class.__init__
    original_rows = state_class.rows

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["orderbook_live_display_guard_installed"] = True
            self.status["orderbook_live_display_guard_version"] = PATCH_VERSION
            self.status["orderbook_live_display_guard_max_age_sec"] = MAX_LIVE_AGE_SEC

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        session = market_session_now()
        phase = str(session.phase or "")
        active = phase in ACTIVE_PHASES
        now = datetime.now().astimezone()
        visible = 0
        hidden_nonlive = 0
        hidden_stale = 0
        hidden_missing = 0

        if active:
            for row in result:
                if not isinstance(row, dict) or not normalize_code(row.get("stock_code")):
                    continue
                ratio = _number(row.get("bid_ask_ratio"))
                received_at = row.get("orderbook_received_at") or row.get(
                    "last_valid_orderbook_at"
                )
                age = _age_sec(received_at, now)
                if ratio is None or ratio <= 0:
                    _hide(row, "live_orderbook_missing_hidden", age)
                    hidden_missing += 1
                    continue
                if not _is_live_source(row):
                    _hide(row, "nonrealtime_orderbook_hidden", age)
                    hidden_nonlive += 1
                    continue
                if age is None or age > MAX_LIVE_AGE_SEC:
                    _hide(row, "stale_realtime_orderbook_hidden", age)
                    hidden_stale += 1
                    continue
                row["orderbook_available"] = True
                row["orderbook_status"] = "live_realtime_ok"
                row["orderbook_display_basis"] = "fresh_live_orderbook"
                row["orderbook_age_sec"] = round(age, 3)
                visible += 1

        with self.lock:
            self.status["orderbook_live_display_guard_phase"] = phase
            self.status["orderbook_live_display_guard_active"] = active
            self.status["orderbook_live_display_visible_count"] = visible
            self.status["orderbook_live_display_hidden_nonlive_count"] = hidden_nonlive
            self.status["orderbook_live_display_hidden_stale_count"] = hidden_stale
            self.status["orderbook_live_display_hidden_missing_count"] = hidden_missing
        return result

    state_class.__init__ = state_init
    state_class.rows = rows
    state_class._stockboard_orderbook_live_display_guard_installed = True
