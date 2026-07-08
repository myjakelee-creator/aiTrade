from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kiwoom_data_provider import (  # noqa: E402
    _first,
    _post_json,
    fetch_trade_value_top100,
    issue_access_token,
)
from realtime_v2.common import (  # noqa: E402
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    now_text,
    to_number,
    trading_date_text,
)
from stockboard_previous_trade_value import previous_trade_value_from_daily_row  # noqa: E402
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


def _previous_trade_value_cache_paths(query_date: str) -> list[Path]:
    return [
        ROOT / "data" / "runtime" / f"previous_trade_value_{query_date}.json",
        RUNTIME_DIR / f"previous_trade_value_{query_date}.json",
    ]


def _load_previous_trade_value_entries(query_date: str) -> dict[str, dict[str, Any]]:
    for path in _previous_trade_value_cache_paths(query_date):
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


def _save_previous_trade_value_entries(query_date: str, entries: dict[str, dict[str, Any]]) -> None:
    if not entries:
        return
    payload = {
        "schema_version": 1,
        "source": "stockboard_v2_build_universe_ka10086_previous_daily_row",
        "query_date": query_date,
        "updated_at": now_text(),
        "entries": entries,
    }
    try:
        atomic_write_json(_previous_trade_value_cache_paths(query_date)[0], payload)
    except Exception:
        pass


def _row_date_text(row: dict[str, Any] | None) -> str:
    if not isinstance(row, dict):
        return ""
    value = _first(row, "date", "dt", "일자", "trd_dd", "bas_dt", "stck_bsop_date")
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _select_previous_trade_value_row(daily_rows: list[Any], query_date: str) -> dict[str, Any] | None:
    """Pick the latest daily row strictly before query_date.

    ka10086 does not always return a usable current-day row for every stock during
    the session.  The previous implementation depended on a current/previous pair;
    when the current row was missing, the previous trade value stayed blank.  For
    the amount-ratio denominator we only need the most recent completed daily row
    before today's trading date, so choose it directly by date when possible.
    """
    rows = [row for row in daily_rows if isinstance(row, dict)]
    if not rows:
        return None
    query_digits = "".join(character for character in str(query_date or "") if character.isdigit())[:8]
    dated_rows: list[tuple[str, dict[str, Any]]] = []
    for row in rows:
        row_date = _row_date_text(row)
        if row_date:
            dated_rows.append((row_date, row))
    if query_digits and dated_rows:
        older_rows = [item for item in dated_rows if item[0] < query_digits]
        if older_rows:
            return sorted(older_rows, key=lambda item: item[0], reverse=True)[0][1]
        not_future_rows = [item for item in dated_rows if item[0] <= query_digits]
        if not_future_rows:
            return sorted(not_future_rows, key=lambda item: item[0], reverse=True)[0][1]
    # Last-resort fallback for undocumented/no-date responses.  Prefer the second
    # row because Kiwoom daily rows usually arrive latest-first with current first.
    if len(rows) >= 2:
        return rows[1]
    return rows[0]


def _request_previous_value_one(access_token: str, code: str, query_date: str, registered_code: str) -> dict[str, Any] | None:
    for attempt in range(2):
        try:
            response = _post_json(
                "/api/dostk/mrkcond",
                {
                    "stk_cd": registered_code,
                    "qry_dt": query_date,
                    "indc_tp": "1",
                },
                {
                    "Authorization": f"Bearer {access_token}",
                    "api-id": "ka10086",
                    "cont-yn": "N",
                    "next-key": "",
                },
            )
            daily_rows = _first(response, "daly_stkpc", "daily_stock_price", "output")
            if not isinstance(daily_rows, list):
                return None
            previous_row = _select_previous_trade_value_row(daily_rows, query_date)
            value_data = previous_trade_value_from_daily_row(previous_row)
            value = to_number(value_data.get("prev_trade_value_eok"))
            if value is None or value <= 0:
                return None
            return {
                **value_data,
                "prev_trade_value_lookup_source": f"ka10086:{registered_code}",
                "stock_code": code,
            }
        except Exception:
            if attempt == 0:
                time.sleep(0.15)
                continue
            return None
    return None


def _fetch_previous_value_regular_first(access_token: str, code: str, query_date: str) -> dict[str, Any] | None:
    # Current policy: use the most reliable regular-session daily close source as
    # the denominator for amount ratio. For NXT-traded stocks this intentionally
    # ignores previous-day NXT pre/aftermarket add-ons when no authoritative NXT
    # previous close amount source is available. _AL remains only a fallback for
    # stocks whose base-code lookup fails.
    candidates = [code, f"{code}_AL"]
    seen = set()
    for registered_code in candidates:
        if registered_code in seen:
            continue
        seen.add(registered_code)
        value_data = _request_previous_value_one(access_token, code, query_date, registered_code)
        if value_data is not None:
            return value_data
        time.sleep(0.05)
    return None


def _attach_direct_previous_values(items: list[dict[str, Any]], query_date: str, access_token: str) -> int:
    missing = [
        item
        for item in items
        if to_number(item.get("prev_trade_value_eok")) is None
    ]
    if not missing:
        return 0
    attached = 0
    try:
        configured_workers = int(os.getenv("STOCKBOARD_V2_PREV_VALUE_WORKERS", "2"))
    except ValueError:
        configured_workers = 2
    max_workers = min(max(1, configured_workers), max(1, len(missing)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _fetch_previous_value_regular_first,
                access_token,
                str(item.get("stock_code")),
                query_date,
            ): item
            for item in missing
            if item.get("stock_code")
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                value_data = future.result()
            except Exception:
                value_data = None
            value = to_number(value_data.get("prev_trade_value_eok")) if isinstance(value_data, dict) else None
            if value is None or value <= 0:
                item.setdefault("prev_trade_value_status", "missing")
                item.setdefault("prev_trade_value_lookup_source", "ka10086_missing")
                continue
            item["prev_trade_value_eok"] = round(float(value), 4)
            item["prev_trade_value_source"] = value_data.get("prev_trade_value_source")
            item["prev_trade_value_status"] = value_data.get("prev_trade_value_status")
            item["prev_trade_value_date"] = value_data.get("prev_trade_value_date")
            item["prev_trade_value_lookup_source"] = value_data.get("prev_trade_value_lookup_source")
            attached += 1
    return attached


def _entry_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    value = to_number(item.get("prev_trade_value_eok"))
    code = normalize_code(item.get("stock_code"))
    if not code or value is None or value <= 0:
        return None
    return {
        "prev_trade_value_eok": round(float(value), 4),
        "prev_trade_value_source": item.get("prev_trade_value_source"),
        "prev_trade_value_status": item.get("prev_trade_value_status"),
        "prev_trade_value_date": item.get("prev_trade_value_date"),
        "prev_trade_value_lookup_source": item.get("prev_trade_value_lookup_source"),
        "stock_code": code,
        "updated_at": now_text(),
    }


def _attach_previous_ranks(items: list[dict[str, Any]], query_date: str, access_token: str) -> tuple[int, int]:
    entries = _load_previous_trade_value_entries(query_date)
    cached_attached = 0
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
        item["prev_trade_value_lookup_source"] = "previous_trade_value_cache"
        cached_attached += 1
    direct_attached = _attach_direct_previous_values(items, query_date, access_token)
    cache_entries = dict(entries)
    for item in items:
        entry = _entry_from_item(item)
        if entry is not None:
            cache_entries[str(entry["stock_code"])] = entry
    _save_previous_trade_value_entries(query_date, cache_entries)
    ranked = sorted(
        [item for item in items if to_number(item.get("prev_trade_value_eok")) is not None],
        key=lambda item: (-(to_number(item.get("prev_trade_value_eok")) or 0), item.get("seed_rank") or 999999),
    )
    for prev_rank, item in enumerate(ranked, start=1):
        item["prev_rank"] = prev_rank
    return cached_attached, direct_attached


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
    cached_previous_count, direct_previous_count = _attach_previous_ranks(items, query_date, token)
    missing_previous_count = len(
        [item for item in items if to_number(item.get("prev_trade_value_eok")) is None]
    )
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
        "previous_trade_value_attached_count": cached_previous_count + direct_previous_count,
        "previous_trade_value_cached_count": cached_previous_count,
        "previous_trade_value_direct_regular_count": direct_previous_count,
        "previous_trade_value_missing_count": missing_previous_count,
        "previous_trade_value_policy": "cache first; ka10086 latest completed daily row before trading date; base-code first; _AL fallback only",
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
    print(f"PREVIOUS_TRADE_VALUE_CACHED={payload['previous_trade_value_cached_count']}")
    print(f"PREVIOUS_TRADE_VALUE_DIRECT_REGULAR={payload['previous_trade_value_direct_regular_count']}")
    print(f"PREVIOUS_TRADE_VALUE_MISSING={payload['previous_trade_value_missing_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
