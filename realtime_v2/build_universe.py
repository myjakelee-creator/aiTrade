from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kiwoom_data_provider import fetch_trade_value_top100, issue_access_token  # noqa: E402
from realtime_v2.common import RUNTIME_DIR, atomic_write_json, normalize_code, now_text, trading_date_text  # noqa: E402
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


def build_universe(limit: int, rank_basis: str) -> dict:
    token = issue_access_token()
    rows, page_counts = fetch_trade_value_top100(token, rank_basis=rank_basis)
    name_map = _name_map_from_csv()
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
    return {
        "schema_version": 1,
        "source": "ka10032_seed_universe_filtered_by_tradable_master",
        "rank_basis": rank_basis,
        "built_at": now_text(),
        "trading_date": trading_date_text(),
        "limit": limit,
        "count": len(items),
        "page_counts": page_counts,
        "filtered_out_not_tradable": filtered_out,
        "tradable_filter_enabled": bool(tradable_codes),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
