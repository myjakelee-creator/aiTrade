"""Write a sidecar program-net snapshot for StockBoard UI overlay.

This script is intentionally outside the StockBoard server startup path. It can
be run in a separate console while StockBoard is already running. The browser
reads docs/assets/program_net_snapshot.json and overlays the 프로(억) column when
this file exists.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from kiwoom_data_provider import fetch_program_net, issue_access_token  # noqa: E402
from stockboard_engine import _query_date  # noqa: E402

KST = timezone(timedelta(hours=9))
DEFAULT_INTERVAL_SEC = 60.0


def _now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("A") and len(text) == 7:
        text = text[1:]
    text = text.replace("_AL", "").replace("_NX", "")
    return text if len(text) == 6 and text.isdigit() else ""


def _clean_values(values: dict[str, Any] | None) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for raw_code, raw_value in (values or {}).items():
        code = _stock_code(raw_code)
        value = _number(raw_value.get("program_net") if isinstance(raw_value, dict) else raw_value)
        if code and value is not None:
            cleaned[code] = value
    return cleaned


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def _snapshot_payload(token: str, query_date: str) -> dict[str, Any]:
    result = fetch_program_net(token, query_date)
    values = _clean_values(result.get("values") if isinstance(result, dict) else {})
    status = "ok"
    if not values:
        status = "empty"
    elif result.get("errors") or result.get("rate_limit"):
        status = "partial"
    return {
        "schema_version": 1,
        "source": "ka90004_sidecar",
        "status": status,
        "query_date": query_date,
        "updated_at": _now_kst(),
        "values": values,
        "count": len(values),
        "market_counts": result.get("market_counts") or {"KOSPI": 0, "KOSDAQ": 0},
        "market_stats": result.get("market_stats") or [],
        "divisor": result.get("divisor"),
        "request_sleep_seconds": result.get("request_sleep_seconds"),
        "errors": result.get("errors") or [],
        "rate_limit": result.get("rate_limit"),
        "raw_samples": result.get("raw_samples") or [],
        "converted_samples": result.get("converted_samples") or [],
    }


def write_snapshot(token: str, query_date: str, docs_output: Path, runtime_output: Path | None) -> dict[str, Any]:
    payload = _snapshot_payload(token, query_date)
    _atomic_write_json(docs_output, payload)
    if runtime_output is not None:
        _atomic_write_json(runtime_output, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard sidecar program-net snapshot writer")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SEC, help="refresh interval seconds; default 60")
    parser.add_argument("--once", action="store_true", help="write once and exit")
    parser.add_argument("--query-date", default="", help="YYYYMMDD query date; default uses stockboard_engine._query_date()")
    parser.add_argument(
        "--docs-output",
        default=str(ROOT_DIR / "docs" / "assets" / "program_net_snapshot.json"),
        help="JSON file served by StockBoard browser UI",
    )
    parser.add_argument(
        "--runtime-output",
        default="",
        help="optional runtime archive path; default data/runtime/program_net/program_net_snapshot_YYYYMMDD.json",
    )
    args = parser.parse_args()

    interval = max(15.0, float(args.interval or DEFAULT_INTERVAL_SEC))
    query_date = "".join(ch for ch in str(args.query_date or _query_date()) if ch.isdigit())[:8]
    if len(query_date) != 8:
        query_date = datetime.now(KST).strftime("%Y%m%d")
    docs_output = Path(args.docs_output)
    runtime_output = Path(args.runtime_output) if args.runtime_output else ROOT_DIR / "data" / "runtime" / "program_net" / f"program_net_snapshot_{query_date}.json"

    token = issue_access_token()
    print("StockBoard program-net sidecar started", flush=True)
    print(f"query_date={query_date}", flush=True)
    print(f"docs_output={docs_output}", flush=True)
    print(f"runtime_output={runtime_output}", flush=True)
    print(f"interval={interval}s", flush=True)

    while True:
        started = time.monotonic()
        try:
            payload = write_snapshot(token, query_date, docs_output, runtime_output)
            print(
                f"{payload['updated_at']} status={payload['status']} count={payload['count']} "
                f"KOSPI={payload['market_counts'].get('KOSPI', 0)} "
                f"KOSDAQ={payload['market_counts'].get('KOSDAQ', 0)}",
                flush=True,
            )
        except Exception as error:
            print(f"{_now_kst()} error={error}", file=sys.stderr, flush=True)
        if args.once:
            return 0
        elapsed = time.monotonic() - started
        time.sleep(max(1.0, interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
