from __future__ import annotations

"""Operator-only diagnostic for StockBoard closed-session metric restoration.

This script performs one local snapshot read. It does not change Worker state, start
collectors, issue Kiwoom requests, or alter SSE cadence.
"""

import argparse
import json
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8765/api/v2/snapshot?limit=300"
ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "rows", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    snapshot = payload.get("snapshot")
    if isinstance(snapshot, dict):
        return _items(snapshot)
    return []


def _fetch(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8-sig"))
    if isinstance(payload, dict):
        return payload
    return {"items": payload if isinstance(payload, list) else []}


def _text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return "-"


def build_report(payload: dict[str, Any], limit: int) -> dict[str, Any]:
    rows = _items(payload)
    prev_status = Counter()
    prev_source = Counter()
    amount_status = Counter()
    one_min_status = Counter()
    counts = Counter()
    samples: list[dict[str, Any]] = []

    for row in rows:
        prev = _number(row.get("prev_trade_value_eok"))
        current = _number(row.get("trade_value_eok"))
        ratio = _number(row.get("amount_ratio"))
        one_min = _number(
            row.get("one_min_trade_value_eok")
            if row.get("one_min_trade_value_eok") not in (None, "")
            else row.get("trade_value_1m_eok")
        )
        counts["rows"] += 1
        counts["prev_trade_value_valid"] += int(prev is not None and prev > 0)
        counts["current_trade_value_valid"] += int(current is not None and current >= 0)
        counts["amount_ratio_valid"] += int(ratio is not None and ratio > 0)
        counts["prev_rank_valid"] += int(_number(row.get("prev_rank")) is not None)
        counts["grade_present"] += int(row.get("grade") not in (None, "", "-"))
        counts["one_min_positive"] += int(one_min is not None and one_min > 0)
        counts["one_min_zero"] += int(one_min == 0)
        counts["bid_ask_ratio_valid"] += int((_number(row.get("bid_ask_ratio")) or 0) > 0)
        counts["execution_strength_valid"] += int((_number(row.get("execution_strength")) or 0) > 0)
        counts["strength_5m_valid"] += int((_number(row.get("strength_5m")) or 0) > 0)

        prev_status[_text(row, "prev_trade_value_status", "amount_ratio_missing_reason")] += 1
        prev_source[_text(row, "prev_trade_value_source")] += 1
        amount_status[_text(row, "amount_ratio_status")] += 1
        one_min_status[_text(row, "one_min_status", "minute_trade_value_status")] += 1

        if len(samples) < max(1, limit):
            samples.append(
                {
                    "stock_code": _text(row, "stock_code", "code"),
                    "stock_name": _text(row, "stock_name", "name"),
                    "trade_value_eok": current,
                    "prev_trade_value_eok": prev,
                    "prev_trade_value_source": _text(row, "prev_trade_value_source"),
                    "prev_trade_value_status": _text(
                        row, "prev_trade_value_status", "amount_ratio_missing_reason"
                    ),
                    "amount_ratio": ratio,
                    "amount_ratio_status": _text(row, "amount_ratio_status"),
                    "prev_rank": row.get("prev_rank"),
                    "grade": row.get("grade"),
                    "one_min_trade_value_eok": one_min,
                    "one_min_status": _text(
                        row, "one_min_status", "minute_trade_value_status"
                    ),
                    "bid_ask_ratio": _number(row.get("bid_ask_ratio")),
                    "execution_strength": _number(row.get("execution_strength")),
                    "execution_strength_source": _text(row, "execution_strength_source"),
                    "strength_5m": _number(row.get("strength_5m")),
                    "strength_source": _text(row, "strength_source"),
                }
            )

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "operator_only_no_production_path_change": True,
        "counts": dict(counts),
        "prev_trade_value_status_counts": dict(prev_status),
        "prev_trade_value_source_counts": dict(prev_source),
        "amount_ratio_status_counts": dict(amount_status),
        "one_min_status_counts": dict(one_min_status),
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    payload = _fetch(args.url, args.timeout)
    report = build_report(payload, args.limit)
    output = (
        Path(args.output)
        if args.output
        else RUNTIME_DIR
        / f"closed_metric_doctor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== StockBoard closed metric doctor ===")
    for key, value in report["counts"].items():
        print(f"{key:32}: {value}")
    print("prev_trade_value_status_counts :", report["prev_trade_value_status_counts"])
    print("prev_trade_value_source_counts :", report["prev_trade_value_source_counts"])
    print("amount_ratio_status_counts     :", report["amount_ratio_status_counts"])
    print("one_min_status_counts          :", report["one_min_status_counts"])
    print("\nCode   Name                 Current    Previous  Ratio  PrevRank Grade OneMin PrevStatus")
    print("------ -------------------- ---------- --------- ------ -------- ----- ------ ------------------------------")
    for item in report["samples"]:
        print(
            f"{item['stock_code']:<6} {item['stock_name'][:20]:<20} "
            f"{str(item['trade_value_eok']):>10} {str(item['prev_trade_value_eok']):>9} "
            f"{str(item['amount_ratio']):>6} {str(item['prev_rank']):>8} "
            f"{str(item['grade']):>5} {str(item['one_min_trade_value_eok']):>6} "
            f"{item['prev_trade_value_status']}"
        )
    print(f"\nJSON_REPORT={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
