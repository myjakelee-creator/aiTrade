from __future__ import annotations

"""Compare the local StockBoard snapshot with one on-demand Kiwoom ka10032 fetch.

This script is never imported by the live worker. It creates no background task and
adds no recurring request. Network access occurs only when the operator explicitly
runs `stockboard_v2_large.cmd price-doctor`.
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kiwoom_data_provider import fetch_trade_value_top100, issue_access_token

RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8765/api/v2/snapshot?limit=300"
DEFAULT_RANK_GATE_LIMIT = 20
DEFAULT_TRADE_VALUE_TOLERANCE_PCT = 0.5


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("StockBoard snapshot response must be an object")
    return payload


def _code(value: Any) -> str:
    text = "".join(str(value or "").split()).upper().split("_", 1)[0]
    if text.startswith("A"):
        text = text[1:]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else ""


def _delta(left: Any, right: Any) -> float | None:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None:
        return None
    return left_number - right_number


def _percent_delta(left: Any, right: Any) -> float | None:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number in (None, 0):
        return None
    return (left_number - right_number) / abs(right_number) * 100.0


def _fmt(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if number is None:
        return "-"
    if digits <= 0:
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def _selected_codes(rows: list[dict[str, Any]], codes_text: str, limit: int) -> list[str]:
    explicit = [_code(item) for item in str(codes_text or "").split(",")]
    explicit = [item for item in explicit if item]
    if explicit:
        return list(dict.fromkeys(explicit))
    result: list[str] = []
    for row in rows:
        code = _code(row.get("stock_code"))
        if code and code not in result:
            result.append(code)
        if len(result) >= limit:
            break
    return result


def _gate_result(
    compared: list[dict[str, Any]],
    *,
    rank_gate_limit: int,
    trade_value_tolerance_pct: float,
) -> tuple[str, dict[str, Any]]:
    gate_rows = [
        row
        for row in compared
        if _number(row.get("local_rank")) is not None
        and int(_number(row.get("local_rank")) or 0) <= rank_gate_limit
    ]
    expected = min(rank_gate_limit, len(compared))
    found_count = sum(bool(row.get("rest_found")) for row in gate_rows)
    rank_comparable = [row for row in gate_rows if row.get("rank_delta") is not None]
    rank_exact = [row for row in rank_comparable if row.get("rank_delta") == 0]
    value_comparable = [
        row for row in gate_rows if row.get("trade_value_delta_pct") is not None
    ]
    value_within = [
        row
        for row in value_comparable
        if abs(float(row["trade_value_delta_pct"])) <= trade_value_tolerance_pct
    ]
    passed = bool(
        len(gate_rows) == expected
        and found_count == expected
        and len(rank_exact) == expected
        and len(value_within) == expected
    )
    return (
        "PASS" if passed else "REVIEW",
        {
            "rank_gate_limit": rank_gate_limit,
            "rank_gate_expected_count": expected,
            "rank_gate_rest_found_count": found_count,
            "rank_gate_exact_count": len(rank_exact),
            "trade_value_tolerance_pct": trade_value_tolerance_pct,
            "trade_value_gate_comparable_count": len(value_comparable),
            "trade_value_gate_within_tolerance_count": len(value_within),
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare StockBoard rank, trade value and price with Kiwoom ka10032"
    )
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--codes", default="")
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL)
    parser.add_argument("--rank-gate-limit", type=int, default=DEFAULT_RANK_GATE_LIMIT)
    parser.add_argument(
        "--trade-value-tolerance-pct",
        type=float,
        default=DEFAULT_TRADE_VALUE_TOLERANCE_PCT,
    )
    args = parser.parse_args()
    limit = max(1, min(100, int(args.limit or 30)))
    rank_gate_limit = max(1, min(limit, int(args.rank_gate_limit or 20)))
    trade_value_tolerance_pct = max(
        0.0, float(args.trade_value_tolerance_pct or 0.0)
    )

    before = _fetch_json(args.snapshot_url)
    before_rows = [row for row in before.get("rows", []) if isinstance(row, dict)]
    codes = _selected_codes(before_rows, args.codes, limit)
    if not codes:
        raise RuntimeError("No local StockBoard codes were available for comparison")

    started = time.perf_counter()
    token = issue_access_token()
    rest_rows, page_counts = fetch_trade_value_top100(token, rank_basis="today")
    fetch_ms = round((time.perf_counter() - started) * 1000.0, 1)

    # Re-read after the REST call so the local side is as close as possible to the
    # completion time of the external snapshot.
    after = _fetch_json(args.snapshot_url)
    local_rows = [row for row in after.get("rows", []) if isinstance(row, dict)]
    local_by_code = {_code(row.get("stock_code")): row for row in local_rows}
    rest_by_code = {_code(row.get("stock_code")): row for row in rest_rows}

    compared: list[dict[str, Any]] = []
    for code in codes:
        local = local_by_code.get(code, {})
        rest = rest_by_code.get(code, {})
        local_rank = _number(local.get("rank"))
        rest_rank = _number(rest.get("original_rank") or rest.get("rank"))
        rank_delta = _delta(local_rank, rest_rank)
        local_price = _number(local.get("price") or local.get("trade_price"))
        rest_price = _number(rest.get("price"))
        local_rate = _number(local.get("change_rate"))
        rest_rate = _number(rest.get("change_rate"))
        local_value = _number(local.get("trade_value_eok"))
        rest_value = _number(rest.get("trade_value_eok"))
        price_delta = _delta(local_price, rest_price)
        rate_delta = _delta(local_rate, rest_rate)
        value_delta_eok = _delta(local_value, rest_value)
        value_delta_pct = _percent_delta(local_value, rest_value)
        compared.append(
            {
                "stock_code": code,
                "stock_name": local.get("stock_name") or rest.get("stock_name") or code,
                "local_rank": local_rank,
                "rest_rank": rest_rank,
                "rank_delta": rank_delta,
                "rank_exact": rank_delta == 0 if rank_delta is not None else None,
                "local_price": local_price,
                "rest_price": rest_price,
                "price_delta": price_delta,
                "price_exact": price_delta == 0 if price_delta is not None else None,
                "local_change_rate": local_rate,
                "rest_change_rate": rest_rate,
                "change_rate_delta": rate_delta,
                "local_trade_value_eok": local_value,
                "rest_trade_value_eok": rest_value,
                "trade_value_delta_eok": value_delta_eok,
                "trade_value_delta_pct": value_delta_pct,
                "local_price_age_sec": _number(local.get("price_age_sec")),
                "local_received_at": local.get("received_at")
                or local.get("price_received_at")
                or local.get("trade_received_at"),
                "local_source_code": local.get("source_code") or local.get("registered_code"),
                "rest_found": bool(rest),
            }
        )

    found = [row for row in compared if row["rest_found"]]
    rank_comparable = [row for row in found if row["rank_delta"] is not None]
    price_comparable = [row for row in found if row["price_delta"] is not None]
    rate_comparable = [row for row in found if row["change_rate_delta"] is not None]
    value_comparable = [row for row in found if row["trade_value_delta_pct"] is not None]
    gate, gate_summary = _gate_result(
        compared,
        rank_gate_limit=rank_gate_limit,
        trade_value_tolerance_pct=trade_value_tolerance_pct,
    )
    summary = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "snapshot_before_ts": before.get("ts"),
        "snapshot_after_ts": after.get("ts"),
        "ka10032_fetch_ms": fetch_ms,
        "ka10032_page_counts": page_counts,
        "requested_count": len(codes),
        "rest_found_count": len(found),
        "rank_comparable_count": len(rank_comparable),
        "rank_exact_count": sum(row["rank_delta"] == 0 for row in rank_comparable),
        "rank_abs_delta_max": max(
            (abs(row["rank_delta"]) for row in rank_comparable), default=None
        ),
        "trade_value_comparable_count": len(value_comparable),
        "trade_value_abs_delta_eok_max": max(
            (abs(row["trade_value_delta_eok"]) for row in value_comparable),
            default=None,
        ),
        "trade_value_abs_delta_pct_max": max(
            (abs(row["trade_value_delta_pct"]) for row in value_comparable),
            default=None,
        ),
        **gate_summary,
        "rank_trade_value_gate": gate,
        "price_comparable_count": len(price_comparable),
        "price_exact_count": sum(row["price_delta"] == 0 for row in price_comparable),
        "price_abs_delta_max": max(
            (abs(row["price_delta"]) for row in price_comparable), default=None
        ),
        "change_rate_abs_delta_max": max(
            (abs(row["change_rate_delta"]) for row in rate_comparable), default=None
        ),
        "operator_only_no_background_load": True,
    }

    print("\n=== StockBoard vs Kiwoom ka10032 rank / trade value comparison ===\n")
    for key, value in summary.items():
        print(f"{key:42}: {value}")

    print(
        "\nLRank KRank dRank Code   Name                 LocalValue  KiwoomValue dValue%"
    )
    print(
        "----- ----- ----- ------ -------------------- ----------- ----------- --------"
    )
    for row in compared:
        print(
            f"{_fmt(row['local_rank'], 0):>5} "
            f"{_fmt(row['rest_rank'], 0):>5} "
            f"{_fmt(row['rank_delta'], 0):>5} "
            f"{row['stock_code']:<6} "
            f"{str(row['stock_name'])[:20]:<20} "
            f"{_fmt(row['local_trade_value_eok'], 2):>11} "
            f"{_fmt(row['rest_trade_value_eok'], 2):>11} "
            f"{_fmt(row['trade_value_delta_pct'], 3):>8}"
        )

    print("\nCode   Name                 LocalPrice   RestPrice    Delta   Local%   Rest%   dRate  AgeSec")
    print("------ -------------------- ------------ ------------ -------- -------- -------- ------ -------")
    for row in compared:
        print(
            f"{row['stock_code']:<6} "
            f"{str(row['stock_name'])[:20]:<20} "
            f"{_fmt(row['local_price'], 0):>12} "
            f"{_fmt(row['rest_price'], 0):>12} "
            f"{_fmt(row['price_delta'], 0):>8} "
            f"{_fmt(row['local_change_rate'], 2):>8} "
            f"{_fmt(row['rest_change_rate'], 2):>8} "
            f"{_fmt(row['change_rate_delta'], 2):>6} "
            f"{_fmt(row['local_price_age_sec'], 1):>7}"
        )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RUNTIME_DIR / f"price_compare_{stamp}.csv"
    json_path = RUNTIME_DIR / f"price_compare_{stamp}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(compared[0].keys()))
        writer.writeheader()
        writer.writerows(compared)
    json_path.write_text(
        json.dumps({"summary": summary, "rows": compared}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nCSV_REPORT={csv_path}")
    print(f"JSON_REPORT={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
