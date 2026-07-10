from __future__ import annotations

from typing import Any

from realtime_v2.common import to_number
from realtime_v2.market_session import market_session_now


def _positive(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    number = float(number)
    return number if number > 0 else None


def _hold_active() -> bool:
    try:
        session = market_session_now().to_dict()
    except Exception:
        return True
    phase = str(session.get("phase") or "").lower()
    accept = bool(session.get("accept_realtime"))
    return phase in {"after_wait", "aftermarket", "closed", "before_market", "weekend", "holiday"} or not accept


def _apply_ohlc_price(row: dict[str, Any]) -> bool:
    ohlc = row.get("ohlc") if isinstance(row.get("ohlc"), dict) else None
    if not ohlc:
        return False
    close = _positive(ohlc.get("close"))
    if close is None:
        return False
    changed = False
    if _positive(row.get("price")) is None:
        row["price"] = round(close, 4)
        changed = True
    if _positive(row.get("trade_price")) is None:
        row["trade_price"] = round(close, 4)
        changed = True
    for source_key, target_key in (
        ("open", "day_open"),
        ("high", "day_high"),
        ("low", "day_low"),
        ("close", "day_close"),
    ):
        value = _positive(ohlc.get(source_key))
        if value is not None and _positive(row.get(target_key)) is None:
            row[target_key] = round(value, 4)
            changed = True
    if changed:
        row["display_hold_ohlc_price_applied"] = True
    return changed


def install(base) -> None:
    state_class = base.State
    if getattr(state_class, "_stockboard_display_hold_ohlc_price_installed", False):
        return
    original_rows = state_class.rows

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        if not _hold_active():
            return result
        applied = 0
        for row in result:
            if isinstance(row, dict) and _apply_ohlc_price(row):
                applied += 1
        self.status["display_hold_ohlc_price_rows"] = applied
        return result

    state_class.rows = rows
    state_class._stockboard_display_hold_ohlc_price_installed = True
