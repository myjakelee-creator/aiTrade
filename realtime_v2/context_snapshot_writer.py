from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import (  # noqa: E402
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    now_text,
    to_number,
    trading_date_text,
)
from realtime_v2.market_supply_last_valid_patch import (  # noqa: E402
    _read_json_with_encoding,
    market_supply_valid,
    normalize_market_supply,
)

OUTPUT_DIR = RUNTIME_DIR
US_MARKET_FILE = OUTPUT_DIR / "us_market.json"
MARKET_SUPPLY_FILE = OUTPUT_DIR / "market_supply.json"
MARKET_SUPPLY_LAST_VALID_FILE = OUTPUT_DIR / "market_supply_last_valid.json"
OHLC_SNAPSHOT_FILE = OUTPUT_DIR / "ohlc_snapshot.json"
STATUS_FILE = OUTPUT_DIR / "context_snapshot_status.json"

_MARKET_SUPPLY_ACCESS_TOKEN: str | None = None
_MARKET_SUPPLY_TOKEN_ISSUED_AT: str | None = None
_MARKET_SUPPLY_TOKEN_ISSUED_MONOTONIC: float | None = None
_MARKET_SUPPLY_TOKEN_ISSUE_COUNT = 0
_MARKET_SUPPLY_TOKEN_REFRESH_COUNT = 0
_MARKET_SUPPLY_TOKEN_LAST_REASON: str | None = None

_AUTH_ERROR_MARKERS = (
    "인증에 실패",
    "인증 실패",
    "authentication failed",
    "invalid access token",
    "invalid token",
    "expired token",
    "token expired",
    "unauthorized",
    "authorization failed",
    "[800",
)

YAHOO_SYMBOLS = {
    "NQ=F": "NQ=F",
    "ES=F": "ES=F",
    "YM=F": "YM=F",
    "QQQ": "QQQ",
    "SOXL": "SOXL",
    "SMH": "SMH",
    "IBB": "IBB",
    "LIT": "LIT",
    "BOTZ": "BOTZ",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    payload, _encoding = _read_json_with_encoding(path)
    return payload if isinstance(payload, dict) else None


def _market_supply_candidates(patterns: list[str]) -> list[Path]:
    candidates: list[Path] = []
    for pattern in patterns:
        candidates.extend(ROOT.glob(pattern))
    candidates = [path for path in candidates if path.is_file()]
    return sorted(
        candidates,
        key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
        reverse=True,
    )


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)


def _market_supply_errors(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []

    result: list[str] = []
    status = payload.get("_status")
    if isinstance(status, dict):
        if status.get("error") not in (None, ""):
            result.append(str(status.get("error")))
        errors = status.get("errors")
        if isinstance(errors, list):
            for item in errors:
                if isinstance(item, dict) and item.get("error") not in (None, ""):
                    result.append(str(item.get("error")))
                elif item not in (None, ""):
                    result.append(str(item))

    for key in ("kospi", "kosdaq", "KOSPI", "KOSDAQ"):
        entry = payload.get(key)
        if isinstance(entry, dict) and entry.get("error") not in (None, ""):
            result.append(str(entry.get("error")))

    deduped: list[str] = []
    seen: set[str] = set()
    for text in result:
        if text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped


def _market_supply_auth_failed(payload: Any) -> bool:
    text = "\n".join(_market_supply_errors(payload)).lower()
    return any(marker.lower() in text for marker in _AUTH_ERROR_MARKERS)


def _issue_market_supply_access_token(issue_access_token, reason: str) -> str:
    global _MARKET_SUPPLY_ACCESS_TOKEN
    global _MARKET_SUPPLY_TOKEN_ISSUED_AT
    global _MARKET_SUPPLY_TOKEN_ISSUED_MONOTONIC
    global _MARKET_SUPPLY_TOKEN_ISSUE_COUNT
    global _MARKET_SUPPLY_TOKEN_REFRESH_COUNT
    global _MARKET_SUPPLY_TOKEN_LAST_REASON

    token = str(issue_access_token() or "").strip()
    if not token:
        raise RuntimeError("Kiwoom token issuer returned an empty token")

    _MARKET_SUPPLY_ACCESS_TOKEN = token
    _MARKET_SUPPLY_TOKEN_ISSUED_AT = now_text()
    _MARKET_SUPPLY_TOKEN_ISSUED_MONOTONIC = time.monotonic()
    _MARKET_SUPPLY_TOKEN_ISSUE_COUNT += 1
    if reason != "initial":
        _MARKET_SUPPLY_TOKEN_REFRESH_COUNT += 1
    _MARKET_SUPPLY_TOKEN_LAST_REASON = reason
    return token


def _market_supply_token_diagnostics() -> dict[str, Any]:
    age_sec = None
    if _MARKET_SUPPLY_TOKEN_ISSUED_MONOTONIC is not None:
        age_sec = round(
            max(0.0, time.monotonic() - _MARKET_SUPPLY_TOKEN_ISSUED_MONOTONIC),
            1,
        )
    return {
        "market_supply_token_issued_at": _MARKET_SUPPLY_TOKEN_ISSUED_AT,
        "market_supply_token_age_sec": age_sec,
        "market_supply_token_issue_count": _MARKET_SUPPLY_TOKEN_ISSUE_COUNT,
        "market_supply_token_refresh_count": _MARKET_SUPPLY_TOKEN_REFRESH_COUNT,
        "market_supply_token_last_reason": _MARKET_SUPPLY_TOKEN_LAST_REASON,
    }


def fetch_yahoo_snapshot(timeout: float = 5.0) -> dict[str, Any]:
    """Use the old StockBoard Yahoo chart implementation path.

    v0.3.x already works through stockboard_server._fetch_yahoo_chart_quote().
    This wrapper only adapts that stable result shape to v2 context JSON.
    """
    from stockboard_server import _fetch_yahoo_chart_quote

    values: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    for label, symbol in YAHOO_SYMBOLS.items():
        try:
            quote_payload = _fetch_yahoo_chart_quote(symbol)
            values[label] = {
                "symbol": symbol,
                "price": quote_payload.get("price"),
                "change_rate": quote_payload.get("change_rate"),
                "as_of": quote_payload.get("as_of"),
                "age_sec": quote_payload.get("age_sec"),
                "freshness": quote_payload.get("freshness"),
                "source": "stockboard_server_yahoo_chart",
            }
        except Exception as error:
            errors.append({"symbol": symbol, "error": str(error)})

    if not values:
        raise RuntimeError(f"Yahoo chart unavailable: {errors[-3:]}")

    return {
        "schema_version": 1,
        "source": "stockboard_server_yahoo_chart",
        "ts": now_text(),
        "values": values,
        **values,
        "errors": errors,
    }


def fetch_live_market_supply_snapshot() -> dict[str, Any]:
    """Fetch valid live KOSPI/KOSDAQ context with one auth-refresh retry.

    Kiwoom market APIs can return a normal-looking dictionary containing only
    unavailable entries when a cached token expires. That response does not raise,
    so semantic validation and explicit authentication-error detection are required.
    """
    global _MARKET_SUPPLY_ACCESS_TOKEN

    from kiwoom_data_provider import fetch_market_supply, issue_access_token

    if not _MARKET_SUPPLY_ACCESS_TOKEN:
        _issue_market_supply_access_token(issue_access_token, "initial")

    attempts: list[dict[str, Any]] = []
    payload: dict[str, Any] | None = None

    for attempt_number in (1, 2):
        try:
            payload = fetch_market_supply(
                _MARKET_SUPPLY_ACCESS_TOKEN,
                trading_date_text(),
            )
        except Exception as error:
            attempts.append(
                {
                    "attempt": attempt_number,
                    "result": "exception",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            if attempt_number == 1:
                _issue_market_supply_access_token(
                    issue_access_token,
                    "request_exception",
                )
                continue
            raise RuntimeError(
                f"live market_supply request failed after token refresh; attempts={attempts}"
            ) from error

        if not isinstance(payload, dict):
            attempts.append(
                {
                    "attempt": attempt_number,
                    "result": "non_dict",
                    "type": type(payload).__name__,
                }
            )
            if attempt_number == 1:
                _issue_market_supply_access_token(
                    issue_access_token,
                    "non_dict_response",
                )
                continue
            break

        normalized = normalize_market_supply(payload)
        if market_supply_valid(normalized):
            normalized.setdefault("schema_version", 1)
            normalized["source"] = "kiwoom_fetch_market_supply"
            normalized["ts"] = now_text()
            normalized["copied_at"] = now_text()
            normalized["source_encoding"] = "runtime_object"
            normalized["token_retry_used"] = attempt_number > 1
            normalized["token_attempts"] = attempts + [
                {"attempt": attempt_number, "result": "valid"}
            ]
            normalized.update(_market_supply_token_diagnostics())
            return normalized

        auth_failed = _market_supply_auth_failed(payload)
        errors = _market_supply_errors(payload)
        attempts.append(
            {
                "attempt": attempt_number,
                "result": "invalid",
                "auth_failed": auth_failed,
                "errors": errors[:8],
            }
        )

        if attempt_number == 1 and auth_failed:
            _MARKET_SUPPLY_ACCESS_TOKEN = None
            _issue_market_supply_access_token(
                issue_access_token,
                "auth_failure_response",
            )
            continue
        break

    raise RuntimeError(
        "live market_supply invalid; preserving last valid snapshot; "
        f"attempts={attempts}"
    )


def copy_latest_market_supply_snapshot() -> dict[str, Any]:
    """Return the newest valid market snapshot, including UTF-16 files."""
    candidates = [MARKET_SUPPLY_LAST_VALID_FILE]
    candidates.extend(
        _market_supply_candidates(
            [
                "data/runtime/market_supply_after_*.json",
                "data/runtime/market_supply_before_*.json",
                "docs/assets/market_supply*.json",
                "docs/assets/stockboard_market_supply*.json",
            ]
        )
    )

    checked: list[str] = []
    for path in candidates:
        if not path.is_file():
            continue
        checked.append(str(path))
        payload, encoding = _read_json_with_encoding(path)
        normalized = normalize_market_supply(payload)
        if not market_supply_valid(normalized):
            continue
        normalized.setdefault("schema_version", 1)
        normalized["source_file"] = str(path)
        normalized["source_encoding"] = encoding
        normalized["copied_at"] = now_text()
        normalized["fallback_checked"] = checked
        return normalized

    raise RuntimeError(f"no valid market_supply snapshot found; checked={checked}")


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
    value = _first(
        row,
        "date",
        "dt",
        "일자",
        "trd_dd",
        "bas_dt",
        "stck_bsop_date",
    )
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _row_ohlc(row: dict[str, Any]) -> dict[str, Any] | None:
    open_price = _num(_first(row, "open_pric", "open", "시가", "stck_oprc"))
    high_price = _num(_first(row, "high_pric", "high", "고가", "stck_hgpr"))
    low_price = _num(_first(row, "low_pric", "low", "저가", "stck_lwpr"))
    close_price = _num(
        _first(
            row,
            "cur_prc",
            "close_pric",
            "close",
            "현재가",
            "종가",
            "stck_clpr",
        )
    )
    if any(
        value is None or value <= 0
        for value in (open_price, high_price, low_price, close_price)
    ):
        return None
    return {
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "source": "ka10086_current_row",
        "date": _row_date(row),
    }


def fetch_ohlc_bootstrap(
    codes_file: Path,
    limit: int = 300,
    sleep_sec: float = 0.12,
) -> dict[str, Any]:
    from kiwoom_data_provider import _post_json, issue_access_token

    if not codes_file.is_file():
        return {
            "schema_version": 1,
            "source": "ka10086_ohlc_bootstrap_AL_first",
            "ts": now_text(),
            "values": {},
            "error": f"codes file not found: {codes_file}",
        }

    codes = [
        normalize_code(line.strip())
        for line in codes_file.read_text(encoding="utf-8-sig").splitlines()
        if normalize_code(line.strip())
    ]
    codes = codes[: max(1, int(limit or 300))]
    token = issue_access_token()
    today = trading_date_text()
    values: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    source_counts = {
        "ka10086_AL_current_row": 0,
        "ka10086_regular_current_row": 0,
    }

    for code in codes:
        found = False
        for query_code, source_name in (
            (f"{code}_AL", "ka10086_AL_current_row"),
            (code, "ka10086_regular_current_row"),
        ):
            try:
                response = _post_json(
                    "/api/dostk/mrkcond",
                    {"stk_cd": query_code, "qry_dt": today, "indc_tp": "1"},
                    {
                        "Authorization": f"Bearer {token}",
                        "api-id": "ka10086",
                        "cont-yn": "N",
                        "next-key": "",
                    },
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
                    if ohlc is None:
                        continue

                    ohlc["source"] = source_name
                    ohlc["query_code"] = query_code
                    ohlc["stock_code"] = code
                    values[code] = ohlc
                    source_counts[source_name] = source_counts.get(source_name, 0) + 1
                    found = True
                    break

                if found:
                    break

            except Exception as error:
                if len(errors) < 20:
                    errors.append(
                        {
                            "stock_code": code,
                            "query_code": query_code,
                            "error": str(error),
                        }
                    )

            if sleep_sec > 0:
                time.sleep(sleep_sec)

        if sleep_sec > 0:
            time.sleep(sleep_sec)

    return {
        "schema_version": 1,
        "source": "ka10086_ohlc_bootstrap_AL_first",
        "ts": now_text(),
        "trading_date": today,
        "count": len(values),
        "source_counts": source_counts,
        "error_count": len(errors),
        "errors": errors,
        "values": values,
    }


def write_status(status: dict[str, Any]) -> None:
    status["ts"] = now_text()
    _atomic_write(STATUS_FILE, status)


def _persist_valid_market_supply(payload: dict[str, Any]) -> None:
    normalized = normalize_market_supply(payload)
    if not market_supply_valid(normalized):
        raise RuntimeError(
            "refusing to overwrite market_supply.json with invalid payload"
        )
    _atomic_write(MARKET_SUPPLY_FILE, normalized)
    _atomic_write(MARKET_SUPPLY_LAST_VALID_FILE, normalized)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="StockBoard v2 low-priority context snapshot writer"
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=float(os.getenv("STOCKBOARD_V2_CONTEXT_INTERVAL_SEC", "30")),
    )
    parser.add_argument(
        "--ohlc-bootstrap",
        action="store_true",
        default=os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_BOOTSTRAP", "1") == "1",
    )
    parser.add_argument(
        "--ohlc-limit",
        type=int,
        default=int(os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_LIMIT", "300")),
    )
    parser.add_argument(
        "--ohlc-sleep-sec",
        type=float,
        default=float(os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_SLEEP_SEC", "0.12")),
    )
    parser.add_argument("--codes-file", default=str(RUNTIME_DIR / "codes.txt"))
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    status = {
        "started_at": now_text(),
        "pid": os.getpid(),
        "interval_sec": args.interval_sec,
    }
    write_status(status)

    if args.ohlc_bootstrap:
        try:
            ohlc_payload = fetch_ohlc_bootstrap(
                Path(args.codes_file),
                args.ohlc_limit,
                args.ohlc_sleep_sec,
            )
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
                status.pop("us_market_error", None)
            except Exception as error:
                status["us_market_status"] = "error"
                status["us_market_error"] = str(error)

            try:
                try:
                    market_payload = fetch_live_market_supply_snapshot()
                    status["market_supply_status"] = "live_ok"
                    status["market_supply_source"] = market_payload.get("source")
                    status["market_supply_token_retry_used"] = market_payload.get(
                        "token_retry_used"
                    )
                    status.pop("market_supply_live_error", None)
                    status.pop("market_supply_source_file", None)
                    status.pop("market_supply_source_encoding", None)
                except Exception as live_error:
                    market_payload = copy_latest_market_supply_snapshot()
                    market_payload["live_error"] = str(live_error)
                    status["market_supply_status"] = "fallback_file"
                    status["market_supply_live_error"] = str(live_error)
                    status["market_supply_source_file"] = market_payload.get(
                        "source_file"
                    )
                    status["market_supply_source_encoding"] = market_payload.get(
                        "source_encoding"
                    )

                _persist_valid_market_supply(market_payload)
                status.pop("market_supply_error", None)
            except Exception as error:
                status["market_supply_status"] = "hold_last_valid"
                status["market_supply_error"] = str(error)

            status.update(_market_supply_token_diagnostics())
            write_status(status)
        except Exception as error:
            write_status({"status": "loop_error", "error": str(error)})
        time.sleep(max(15.0, float(args.interval_sec or 30.0)))


if __name__ == "__main__":
    raise SystemExit(main())
