from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, normalize_code, now_text, to_number, trading_date_text  # noqa: E402

OUTPUT_DIR = RUNTIME_DIR
US_MARKET_FILE = OUTPUT_DIR / "us_market.json"
MARKET_SUPPLY_FILE = OUTPUT_DIR / "market_supply.json"
OHLC_SNAPSHOT_FILE = OUTPUT_DIR / "ohlc_snapshot.json"
STATUS_FILE = OUTPUT_DIR / "context_snapshot_status.json"

YAHOO_SYMBOLS = {
    "NASDAQ": "^IXIC",
    "QQQ": "QQQ",
    "SOXL": "SOXL",
    "SMH": "SMH",
    "IBB": "IBB",
    "LIT": "LIT",
    "BOTZ": "BOTZ",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                return payload
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _latest_file(patterns: list[str]) -> Path | None:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(ROOT.glob(pattern))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)


def fetch_yahoo_snapshot(timeout: float = 5.0) -> dict[str, Any]:
    symbols = list(YAHOO_SYMBOLS.values())
    url = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + quote(",".join(symbols), safe=",^")
    with urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    results = payload.get("quoteResponse", {}).get("result", [])
    by_symbol = {row.get("symbol"): row for row in results if isinstance(row, dict)}
    values: dict[str, Any] = {}
    for label, symbol in YAHOO_SYMBOLS.items():
        row = by_symbol.get(symbol, {})
        values[label] = {
            "symbol": symbol,
            "price": row.get("regularMarketPrice"),
            "change": row.get("regularMarketChange"),
            "change_rate": row.get("regularMarketChangePercent"),
            "market_time": row.get("regularMarketTime"),
            "source": "yahoo_quote",
        }
    return {
        "schema_version": 1,
        "source": "yahoo_quote",
        "ts": now_text(),
        "values": values,
        **values,
    }


def copy_latest_market_supply_snapshot() -> dict[str, Any]:
    latest = _latest_file(
        [
            "data/runtime/market_supply_after_*.json",
            "data/runtime/market_supply_before_*.json",
            "docs/assets/market_supply*.json",
            "docs/assets/stockboard_market_supply*.json",
        ]
    )
    if latest is None:
        return {
            "schema_version": 1,
            "source": "missing",
            "ts": now_text(),
            "message": "no market_supply snapshot source file found",
        }
    payload = _read_json(latest) or {}
    payload.setdefault("schema_version", 1)
    payload["source_file"] = str(latest)
    payload["copied_at"] = now_text()
    return payload


def _num(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    return abs(float(number))


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _row_date(row: dict[str, Any]) -> str:
    value = _first(row, "date", "dt", "일자", "trd_dd", "bas_dt", "stck_bsop_date")
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _row_ohlc(row: dict[str, Any]) -> dict[str, Any] | None:
    open_price = _num(_first(row, "open_pric", "open", "시가", "stck_oprc"))
    high_price = _num(_first(row, "high_pric", "high", "고가", "stck_hgpr"))
    low_price = _num(_first(row, "low_pric", "low", "저가", "stck_lwpr"))
    close_price = _num(_first(row, "cur_prc", "close_pric", "close", "현재가", "종가", "stck_clpr"))
    if any(value is None or value <= 0 for value in (open_price, high_price, low_price, close_price)):
        return None
    return {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "source": "ka10086_current_row",
        "date": _row_date(row),
    }


def fetch_ohlc_bootstrap(codes_file: Path, limit: int = 300, sleep_sec: float = 0.12) -> dict[str, Any]:
    from kiwoom_data_provider import _post_json, issue_access_token  # local import: optional path only

    if not codes_file.is_file():
        return {"schema_version": 1, "source": "ka10086_ohlc_bootstrap", "ts": now_text(), "values": {}, "error": f"codes file not found: {codes_file}"}
    codes = [normalize_code(line.strip()) for line in codes_file.read_text(encoding="utf-8-sig").splitlines() if normalize_code(line.strip())]
    codes = codes[: max(1, int(limit or 300))]
    token = issue_access_token()
    today = trading_date_text()
    values: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for code in codes:
        try:
            response = _post_json(
                "/api/dostk/mrkcond",
                {"stk_cd": code, "qry_dt": today, "indc_tp": "1"},
                {"Authorization": f"Bearer {token}", "api-id": "ka10086", "cont-yn": "N", "next-key": ""},
            )
            rows = _first(response, "daly_stkpc", "daily_stock_price", "output")
            if not isinstance(rows, list):
                continue
            candidates = [row for row in rows if isinstance(row, dict)]
            candidates.sort(key=lambda row: _row_date(row), reverse=True)
            for row in candidates:
                row_date = _row_date(row)
                if row_date and row_date > today:
                    continue
                ohlc = _row_ohlc(row)
                if ohlc is not None:
                    values[code] = ohlc
                    break
        except Exception as error:
            if len(errors) < 20:
                errors.append({"stock_code": code, "error": str(error)})
        if sleep_sec > 0:
            time.sleep(sleep_sec)
    return {
        "schema_version": 1,
        "source": "ka10086_ohlc_bootstrap",
        "ts": now_text(),
        "trading_date": today,
        "count": len(values),
        "error_count": len(errors),
        "errors": errors,
        "values": values,
    }


def write_status(status: dict[str, Any]) -> None:
    status["ts"] = now_text()
    _atomic_write(STATUS_FILE, status)


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard v2 low-priority context snapshot writer")
    parser.add_argument("--interval-sec", type=float, default=float(os.getenv("STOCKBOARD_V2_CONTEXT_INTERVAL_SEC", "30")))
    parser.add_argument("--ohlc-bootstrap", action="store_true", default=os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_BOOTSTRAP", "1") == "1")
    parser.add_argument("--ohlc-limit", type=int, default=int(os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_LIMIT", "300")))
    parser.add_argument("--ohlc-sleep-sec", type=float, default=float(os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_SLEEP_SEC", "0.12")))
    parser.add_argument("--codes-file", default=str(RUNTIME_DIR / "codes.txt"))
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    status = {"started_at": now_text(), "pid": os.getpid(), "interval_sec": args.interval_sec}
    write_status(status)

    if args.ohlc_bootstrap:
        try:
            ohlc_payload = fetch_ohlc_bootstrap(Path(args.codes_file), args.ohlc_limit, args.ohlc_sleep_sec)
            _atomic_write(OHLC_SNAPSHOT_FILE, ohlc_payload)
            status["ohlc_count"] = ohlc_payload.get("count")
            status["ohlc_error_count"] = ohlc_payload.get("error_count")
        except Exception as error:
            status["ohlc_error"] = str(error)
        write_status(status)

    while True:
        try:
            try:
                us_payload = fetch_yahoo_snapshot()
                _atomic_write(US_MARKET_FILE, us_payload)
                status["us_market_status"] = "ok"
            except Exception as error:
                status["us_market_status"] = "error"
                status["us_market_error"] = str(error)

            try:
                market_payload = copy_latest_market_supply_snapshot()
                _atomic_write(MARKET_SUPPLY_FILE, market_payload)
                status["market_supply_status"] = "ok"
                status["market_supply_source_file"] = market_payload.get("source_file")
            except Exception as error:
                status["market_supply_status"] = "error"
                status["market_supply_error"] = str(error)

            write_status(status)
        except Exception as error:
            write_status({"status": "loop_error", "error": str(error)})
        time.sleep(max(15.0, float(args.interval_sec or 30.0)))


if __name__ == "__main__":
    raise SystemExit(main())
