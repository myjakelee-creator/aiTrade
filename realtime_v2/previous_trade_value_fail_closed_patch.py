from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

VERSION = "previous_trade_value_fail_closed_v3"
MIN_PLAUSIBLE_EOK = Decimal("1")


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _reject(result: dict[str, Any], reason: str) -> dict[str, Any]:
    rejected = dict(result)
    rejected["prev_trade_value_rejected_value_eok"] = result.get("prev_trade_value_eok")
    rejected["prev_trade_value_rejected_source"] = result.get("prev_trade_value_source")
    rejected["prev_trade_value_eok"] = None
    rejected["prev_trade_value_source"] = "unavailable"
    rejected["prev_trade_value_status"] = reason
    rejected["prev_trade_value_conversion_version"] = VERSION
    return rejected


def install() -> None:
    import stockboard_previous_trade_value as target

    if getattr(target, "_previous_trade_value_fail_closed_patch_installed", False):
        return

    original = target.previous_trade_value_from_daily_row
    target.PREVIOUS_VALUE_CONVERSION_VERSION = VERSION

    def previous_trade_value_fail_closed(previous_row):
        result = dict(original(previous_row))
        result["prev_trade_value_conversion_version"] = VERSION

        value = _decimal_or_none(result.get("prev_trade_value_eok"))
        source = str(result.get("prev_trade_value_source") or "")
        status = str(result.get("prev_trade_value_status") or "")

        if value is None or value <= 0:
            return result

        if source == "ka10086_trade_value_million" or status == "ok_no_crosscheck":
            return _reject(result, "missing_no_independent_crosscheck")

        if value < MIN_PLAUSIBLE_EOK:
            return _reject(result, "missing_below_plausibility_floor")

        return result

    target.previous_trade_value_from_daily_row = previous_trade_value_fail_closed
    target._previous_trade_value_fail_closed_patch_installed = True


__all__ = ["MIN_PLAUSIBLE_EOK", "VERSION", "install"]
