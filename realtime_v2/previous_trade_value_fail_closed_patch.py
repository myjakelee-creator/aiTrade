from __future__ import annotations

import atexit
import json
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

VERSION = "previous_trade_value_fail_closed_v3"
MIN_PLAUSIBLE_EOK = Decimal("1")
MAX_CURRENT_TO_PREVIOUS_RATIO = Decimal("1000")
_ROOT = Path(__file__).resolve().parents[1]
_INSTALLED = False


def _decimal_or_none(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _rejection_reason(item: dict[str, Any]) -> str:
    value = _decimal_or_none(item.get("prev_trade_value_eok"))
    if value is None or value <= 0:
        return ""

    source = str(item.get("prev_trade_value_source") or "")
    status = str(item.get("prev_trade_value_status") or "")
    if source == "ka10086_trade_value_million" or status == "ok_no_crosscheck":
        return "missing_no_independent_crosscheck"

    if value < MIN_PLAUSIBLE_EOK:
        return "missing_below_plausibility_floor"

    current = _decimal_or_none(
        item.get("seed_trade_value_eok") or item.get("trade_value_eok")
    )
    if current is not None and current > 0 and current / value > MAX_CURRENT_TO_PREVIOUS_RATIO:
        return "missing_current_to_previous_ratio_exceeds_limit"

    return ""


def _clear_previous_value(item: dict[str, Any], reason: str) -> None:
    item["prev_trade_value_rejected_value_eok"] = item.get("prev_trade_value_eok")
    item["prev_trade_value_rejected_source"] = item.get("prev_trade_value_source")
    for key in (
        "prev_trade_value_eok",
        "previous_trade_value_eok",
        "yesterday_trade_value_eok",
        "prev_rank",
    ):
        item.pop(key, None)
    item["prev_trade_value_source"] = "unavailable"
    item["prev_trade_value_status"] = reason
    item["prev_trade_value_conversion_version"] = VERSION
    item["amount_ratio_missing_reason"] = reason


def sanitize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return payload, set()

    rejected_codes: set[str] = set()
    reasons: Counter[str] = Counter()
    for item in items:
        if not isinstance(item, dict):
            continue
        reason = _rejection_reason(item)
        if not reason:
            continue
        code = str(item.get("stock_code") or "").strip()
        if code:
            rejected_codes.add(code)
        reasons[reason] += 1
        _clear_previous_value(item, reason)

    for item in items:
        if isinstance(item, dict):
            item.pop("prev_rank", None)
    ranked = sorted(
        [
            item
            for item in items
            if isinstance(item, dict)
            and (_decimal_or_none(item.get("prev_trade_value_eok")) or Decimal("0")) > 0
        ],
        key=lambda item: (
            -(_decimal_or_none(item.get("prev_trade_value_eok")) or Decimal("0")),
            item.get("seed_rank") or 999999,
        ),
    )
    for rank, item in enumerate(ranked, start=1):
        item["prev_rank"] = rank

    payload["previous_trade_value_fail_closed_version"] = VERSION
    payload["previous_trade_value_fail_closed_rejected_count"] = len(rejected_codes)
    payload["previous_trade_value_fail_closed_reason_counts"] = dict(reasons)
    payload["previous_trade_value_attached_count"] = len(ranked)
    payload["previous_trade_value_missing_count"] = len(items) - len(ranked)
    return payload, rejected_codes


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _output_path_from_argv() -> Path:
    args = list(sys.argv[1:])
    for index, value in enumerate(args):
        if value == "--output" and index + 1 < len(args):
            return Path(args[index + 1])
        if value.startswith("--output="):
            return Path(value.split("=", 1)[1])
    return _ROOT / "data" / "runtime" / "stockboard_v2" / "universe.json"


def _sanitize_cache(path: Path, rejected_codes: set[str]) -> None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return
    changed = False
    for code in rejected_codes:
        if code in entries:
            entries.pop(code, None)
            changed = True
    payload["fail_closed_version"] = VERSION
    payload["fail_closed_rejected_codes"] = sorted(rejected_codes)
    if changed or rejected_codes:
        _atomic_write(path, payload)


def sanitize_universe_output() -> None:
    output = _output_path_from_argv()
    try:
        payload = json.loads(output.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    payload, rejected_codes = sanitize_payload(payload)
    _atomic_write(output, payload)

    trading_date = str(payload.get("trading_date") or "").strip()
    if len(trading_date) == 8 and trading_date.isdigit():
        for path in (
            _ROOT / "data" / "runtime" / f"previous_trade_value_{trading_date}.json",
            _ROOT
            / "data"
            / "runtime"
            / "stockboard_v2"
            / f"previous_trade_value_{trading_date}.json",
        ):
            _sanitize_cache(path, rejected_codes)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    if Path(sys.argv[0]).name.lower() == "build_universe.py":
        atexit.register(sanitize_universe_output)


__all__ = [
    "MAX_CURRENT_TO_PREVIOUS_RATIO",
    "MIN_PLAUSIBLE_EOK",
    "VERSION",
    "install",
    "sanitize_payload",
    "sanitize_universe_output",
]
