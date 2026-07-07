from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kiwoom_data_provider import fetch_trade_value_top100, issue_access_token  # noqa: E402
from realtime_v2.common import RUNTIME_DIR, atomic_write_json, normalize_code, now_text, to_number, trading_date_text  # noqa: E402
from stockboard_store import _load_tradable_stock_codes  # noqa: E402


def _name_map_from_csv() -> dict[str, str]:
    candidates = [
        ROOT / "docs" / "tradable_stock_master.csv",
        ROOT / "tradable_stock_master.csv",
        ROOT / "data" / "tradable_stock_master.csv",
        ROOT / "data" / "stock_master.csv",
    ]
    result: dict[str, str] = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    code = normalize_code(
                        row.get("종목코드") or row.get("stock_code") or row.get("code")
                    )
                    name = str(row.get("종목명") or row.get("stock_name") or row.get("name") or "").strip()
                    if code and name:
                        result[code] = name
        except OSError:
            continue
    return result


def _load_previous_trade_value_entries(query_date: str) -> dict[str, dict[str, Any]]:
    candidates = [
        ROOT / "data" / "runtime" / f"previous_trade_value_{query_date}.json",
        RUNTIME_DIR / f"previous_trade_value_{query_date}.json",
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            entries = payload.get("entries") if isinstance(payload, dict) else None
            if isinstance(entries, dict):
                result = {}
                for raw_code, entry in entries.items():
                    code = normalize_code(raw_code)
                    if code and isinstance(entry, dict):
                        result[code] = dict(entry)
                return result
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def _attach_previous_ranks(items: list[dict[str, Any]], query_date: str) -> int:
    entries = _load_previous_trade_value_entries(query_date)
    attached = 0
    for item in items:
        code = item.get("stock_code")
        entry = entries.get(str(code)) if code else None
        previous_value = to_number(entry.get("prev_trade_value_eok")) if isinstance(entry, dict) else None
        if previous_value is None or previous_value <= 0:
            continue
        item["prev_trade_value_eok"] = round(float(previous_value), 4)
        item["prev_trade_value_source"] = entry.get("prev_trade_value_source")
        item["prev_trade_value_status"] = entry.get("prev_trade_value_status")
        item["prev_trade_value_date"] = entry.get("prev_trade_value_date")
        attached += 1
    ranked = sorted(
        [item for item in items if to_number(item.get("prev_trade_value_eok")) is not None],
        key=lambda item: (-(to_number(item.get("prev_trade_value_eok")) or 0), item.get("seed_rank") or 999999),
    )
    for prev_rank, item in enumerate(ranked, start=1):
        item["prev_rank"] = prev_rank
    return attached


def build_universe(limit: int, rank_basis: str) -> dict:
    token = issue_access_token()
    rows, page_counts = fetch_trade_value_top100(token, rank_basis=rank_basis)
    name_map = _name_map_from_csv()
    query_date = trading_date_text()
    try:
        tradable_codes = _load_tradable_stock_codes()
    except Exception:
        tradable_codes = set()
    items = []
    seen = set()
    filtered_out = 0
    for raw_rank, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        code = normalize_code(row.get("stock_code") or row.get("code"))
        if not code or code in seen:
            continue
        if tradable_codes and code not in tradable_codes:
            filtered_out += 1
            continue
        seen.add(code)
        name = str(row.get("stock_name") or row.get("name") or name_map.get(code) or code).strip()
        items.append(
            {
                "stock_code": code,
                "stock_name": name,
                "seed_rank": raw_rank,
                "original_rank": row.get("original_rank") or raw_rank,
                "seed_trade_value_eok": row.get("trade_value_eok"),
                "seed_change_rate": row.get("change_rate"),
                "seed_price": row.get("price"),
            }
        )
        if len(items) >= limit:
            break
    previous_attached_count = _attach_previous_ranks(items, query_date)
    return {
        "schema_version": 1,
        "source": "ka10032_seed_universe_filtered_by_tradable_master",
        "rank_basis": rank_basis,
        "built_at": now_text(),
        "trading_date": query_date,
        "limit": limit,
        "count": len(items),
        "page_counts": page_counts,
        "filtered_out_not_tradable": filtered_out,
        "tradable_filter_enabled": bool(tradable_codes),
        "previous_trade_value_attached_count": previous_attached_count,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build StockBoard v2 seed universe")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--rank-basis", default="today", choices=["today", "auto"])
    parser.add_argument("--output", default=str(RUNTIME_DIR / "universe.json"))
    args = parser.parse_args()

    payload = build_universe(max(1, int(args.limit)), args.rank_basis)
    output = Path(args.output)
    atomic_write_json(output, payload)
    codes_output = output.with_name("codes.txt")
    codes_output.write_text("\n".join(item["stock_code"] for item in payload["items"]) + "\n", encoding="utf-8")
    print(f"UNIVERSE_FILE={output}")
    print(f"CODES_FILE={codes_output}")
    print(f"UNIVERSE_COUNT={payload['count']}")
    print(f"FILTERED_OUT_NOT_TRADABLE={payload['filtered_out_not_tradable']}")
    print(f"PREVIOUS_TRADE_VALUE_ATTACHED={payload['previous_trade_value_attached_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
