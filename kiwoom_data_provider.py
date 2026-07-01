import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Event, RLock, Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stockboard_engine import (
    _build_ohlc, _daily_row_date, _first, _market_flow_number, _million_to_eok,
    _normalize_row, _ohlc_row_sample, _program_net_eok, _program_net_eok_divisor,
    _recent_dates, _required_market_count, _required_market_number,
    _select_daily_rows, _stock_code, normalize_kiwoom_price,
)


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name, default=0):
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(str(value).strip())
    except ValueError:
        return default


def _normalize_trading_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    digits = "".join(ch for ch in text[:10] if ch.isdigit())
    if len(digits) == 8:
        return digits
    return ""


def _load_dotenv():
    supported_keys = {"KIWOOM_APP_KEY", "KIWOOM_SECRET_KEY"}
    candidates = (Path(r"C:\aiTrade\.env"), Path(__file__).resolve().parent / ".env")
    loaded_paths = set()

    for env_path in candidates:
        resolved_path = env_path.resolve()
        if resolved_path in loaded_paths or not env_path.is_file():
            continue
        loaded_paths.add(resolved_path)

        with env_path.open("r", encoding="utf-8-sig") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key in supported_keys:
                    os.environ.setdefault(key, value.strip())


_load_dotenv()


API_BASE_URL = os.getenv("KIWOOM_API_BASE_URL", "https://api.kiwoom.com").rstrip("/")
KRW_PER_EOK = 100_000_000
LARGE_TRADE_THRESHOLD_KRW = 50_000_000
LARGE_TRADE_THRESHOLD_EOK = LARGE_TRADE_THRESHOLD_KRW / KRW_PER_EOK


class KiwoomAPIError(RuntimeError):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Kiwoom API HTTP {status_code}: {detail}")


def _post_json(path, payload, headers=None, return_headers=False):
    request_headers = {"Content-Type": "application/json;charset=UTF-8"}
    request_headers.update(headers or {})
    request = Request(
        f"{API_BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
            return (body, response.headers) if return_headers else body
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise KiwoomAPIError(error.code, detail) from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"Kiwoom API request failed: {error}") from error


def issue_access_token():
    app_key = os.getenv("KIWOOM_APP_KEY")
    secret_key = os.getenv("KIWOOM_SECRET_KEY")
    if not app_key or not secret_key:
        raise RuntimeError("KIWOOM_APP_KEY and KIWOOM_SECRET_KEY are required")

    response = _post_json(
        "/oauth2/token",
        {
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": secret_key,
        },
    )
    token = response.get("token") or response.get("access_token")
    if not token:
        raise RuntimeError(f"Kiwoom token response did not contain a token: {response}")
    return token


def _top100_original_order_key(index_and_row):
    raw_index, row = index_and_row
    original_rank = row.get("original_rank")
    if isinstance(original_rank, bool) or not isinstance(original_rank, (int, float)):
        original_rank = None
    return (
        original_rank is None,
        original_rank if original_rank is not None else raw_index,
        raw_index,
    )


def fetch_trade_value_top100(access_token):
    """Fetch up to three ka10032 pages and return at most 300 unique rows."""
    page_counts = [0, 0, 0]
    collected_rows = []
    continuation = "N"
    next_key = ""

    for page_index in range(3):
        response, response_headers = _post_json(
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": "000",
                "mang_stk_incls": "0",
                "stex_tp": "3",
            },
            {
                "Authorization": f"Bearer {access_token}",
                "api-id": "ka10032",
                "cont-yn": continuation,
                "next-key": next_key,
            },
            return_headers=True,
        )
        raw_rows = _first(
            response,
            "trde_prica_upper",
            "trade_value_top",
            "output",
        )
        if not isinstance(raw_rows, list):
            raise RuntimeError(f"Unexpected ka10032 response: {response}")

        page_rows = [row for row in raw_rows[:100] if isinstance(row, dict)]
        page_counts[page_index] = len(page_rows)
        collected_rows.extend(_normalize_row(row) for row in page_rows)

        continuation = response_headers.get("cont-yn", "").upper()
        next_key = response_headers.get("next-key", "")
        if continuation != "Y" or not next_key:
            break

    unique_rows = []
    seen_codes = set()
    for row in collected_rows:
        stock_code = row["stock_code"]
        if stock_code and stock_code not in seen_codes:
            seen_codes.add(stock_code)
            unique_rows.append(row)

    rows = [
        row
        for _, row in sorted(
            enumerate(unique_rows),
            key=_top100_original_order_key,
        )
    ][:300]
    return rows, page_counts


def fetch_program_net(access_token, query_date):
    """Fetch ka90004 for KOSPI/KOSDAQ and map stock codes to program net eok."""
    divisor = _program_net_eok_divisor()
    sleep_text = os.getenv("KIWOOM_PROGRAM_NET_REQUEST_SLEEP_SEC", "0.25")
    try:
        sleep_seconds = Decimal(sleep_text)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_PROGRAM_NET_REQUEST_SLEEP_SEC must be a non-negative number"
        ) from error
    if not sleep_seconds.is_finite() or sleep_seconds < 0:
        raise RuntimeError(
            "KIWOOM_PROGRAM_NET_REQUEST_SLEEP_SEC must be a non-negative number"
        )
    sleep_seconds = float(sleep_seconds)
    program_net_by_code = {}
    market_stats = []
    market_counts = {"KOSPI": 0, "KOSDAQ": 0}
    raw_samples = []
    converted_samples = []
    errors = []
    rate_limit = None

    for market_name, market_type in (("KOSPI", "P00101"), ("KOSDAQ", "P10102")):
        continuation = "N"
        next_key = ""
        seen_next_keys = set()
        page_count = 0
        row_count = 0
        market_values = {}
        market_raw_samples = []
        market_converted_samples = []
        market_error = None
        market_rate_limited = False

        while True:
            try:
                response, response_headers = _post_json(
                    "/api/dostk/stkinfo",
                    {
                        "dt": query_date,
                        "mrkt_tp": market_type,
                        "stex_tp": "3",
                    },
                    {
                        "Authorization": f"Bearer {access_token}",
                        "api-id": "ka90004",
                        "cont-yn": continuation,
                        "next-key": next_key,
                    },
                    return_headers=True,
                )
            except KiwoomAPIError as error:
                market_error = f"HTTP {error.status_code}: {error.detail}"
                if error.status_code == 429:
                    market_rate_limited = True
                    if rate_limit is None:
                        rate_limit = {"market": market_name, "page": page_count + 1}
                    print(
                        f"warning: ka90004 HTTP 429 at market {market_name}, "
                        f"page {page_count + 1}; keeping partial market result",
                        file=sys.stderr,
                    )
                break
            except (RuntimeError, ValueError) as error:
                market_error = f"{type(error).__name__}: {error}"
                print(
                    f"warning: ka90004 market {market_name} skipped: {error}",
                    file=sys.stderr,
                )
                break

            if response.get("return_code") not in (None, 0, "0"):
                market_error = f"API failed: {response}"
                print(
                    f"warning: ka90004 market {market_name} skipped: "
                    f"return_code={response.get('return_code')}",
                    file=sys.stderr,
                )
                break

            raw_rows = _first(
                response,
                "stk_prm_trde_prst",
                "stock_program_trade_status",
                "output",
            )
            if not isinstance(raw_rows, list):
                market_error = f"Unexpected response: {response}"
                print(
                    f"warning: ka90004 market {market_name} skipped: "
                    "missing stk_prm_trde_prst list",
                    file=sys.stderr,
                )
                break

            page_count += 1
            page_rows = [row for row in raw_rows if isinstance(row, dict)]
            row_count += len(page_rows)
            for row in page_rows:
                stock_code = _stock_code(_first(row, "stk_cd", "stock_code", "\uc885\ubaa9\ucf54\ub4dc"))
                raw_value = _first(
                    row,
                    "netprps_prica",
                    "program_net",
                    "\ud504\ub85c\uadf8\ub7a8\uc21c\ub9e4\uc218\ub300\uae08",
                )
                converted_value = _program_net_eok(raw_value, divisor)
                if stock_code and converted_value is not None:
                    market_values[stock_code] = converted_value
                    if len(market_raw_samples) < 5:
                        market_raw_samples.append(
                            {"stock_code": stock_code, "netprps_prica": raw_value}
                        )
                        market_converted_samples.append(
                            {"stock_code": stock_code, "program_net": converted_value}
                        )

            continuation = response_headers.get("cont-yn", "").upper()
            next_key = response_headers.get("next-key", "")
            if continuation != "Y" or not next_key:
                break
            if next_key in seen_next_keys:
                market_error = f"Repeated next-key: {next_key}"
                print(
                    f"warning: ka90004 market {market_name} skipped: "
                    f"repeated next-key {next_key}",
                    file=sys.stderr,
                )
                break
            seen_next_keys.add(next_key)
            if sleep_seconds:
                time.sleep(sleep_seconds)

        if market_error is None or market_rate_limited:
            program_net_by_code.update(market_values)
            market_counts[market_name] = len(market_values)
            remaining_sample_count = 5 - len(raw_samples)
            if remaining_sample_count > 0:
                raw_samples.extend(market_raw_samples[:remaining_sample_count])
                converted_samples.extend(
                    market_converted_samples[:remaining_sample_count]
                )
        else:
            errors.append({"market": market_name, "error": market_error})

        market_stats.append(
            {
                "market": market_name,
                "pages": page_count,
                "rows": row_count,
                "count": market_counts[market_name],
                "error": market_error,
                "rate_limited": market_rate_limited,
            }
        )
        if market_rate_limited:
            time.sleep(max(1.0, sleep_seconds))

    return {
        "values": program_net_by_code,
        "market_stats": market_stats,
        "market_counts": market_counts,
        "raw_samples": raw_samples,
        "converted_samples": converted_samples,
        "divisor": divisor,
        "request_sleep_seconds": sleep_seconds,
        "errors": errors,
        "rate_limit": rate_limit,
    }


def _foreign_sum_eok_divisor():
    divisor_text = os.getenv("KIWOOM_FOREIGN_SUM_EOK_DIVISOR", "100")
    try:
        divisor = Decimal(divisor_text)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_FOREIGN_SUM_EOK_DIVISOR must be a positive number"
        ) from error
    if not divisor.is_finite() or divisor <= 0:
        raise RuntimeError(
            "KIWOOM_FOREIGN_SUM_EOK_DIVISOR must be a positive number"
        )
    return divisor


def _foreign_sum_eok(value, divisor):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("--"):
        text = "-" + text[2:]
    elif text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    eok = number / divisor
    return float(eok)


def fetch_foreign_sum(access_token, query_date):
    """Fetch ka10037 and map normalized stock codes to foreign net buy eok."""
    enabled = os.getenv("KIWOOM_FOREIGN_SUM_ENABLED", "1").strip() == "1"
    divisor = _foreign_sum_eok_divisor()
    foreign_sum_by_code = {}
    stats = {
        "enabled": enabled,
        "query_date": query_date,
        "market_counts": {"KOSPI": 0, "KOSDAQ": 0},
        "joined_count": 0,
        "errors": [],
        "rate_limit": None,
        "raw_samples": [],
        "converted_samples": [],
        "divisor": divisor,
    }
    if not enabled:
        return {"values": foreign_sum_by_code, "stats": stats}

    for market_name, market_type in (("KOSPI", "001"), ("KOSDAQ", "101")):
        continuation = "N"
        next_key = ""
        seen_next_keys = set()
        page_count = 0
        market_values = {}
        market_raw_samples = []
        market_converted_samples = []
        market_error = None

        while True:
            try:
                response, response_headers = _post_json(
                    "/api/dostk/rkinfo",
                    {
                        "mrkt_tp": market_type,
                        "dt": "0",
                        "trde_tp": "0",
                        "sort_tp": "0",
                        "stex_tp": "3",
                    },
                    {
                        "Authorization": f"Bearer {access_token}",
                        "api-id": "ka10037",
                        "cont-yn": continuation,
                        "next-key": next_key,
                    },
                    return_headers=True,
                )
            except KiwoomAPIError as error:
                market_error = f"HTTP {error.status_code}: {error.detail}"
                if error.status_code == 429 and stats["rate_limit"] is None:
                    stats["rate_limit"] = {
                        "market": market_name,
                        "page": page_count + 1,
                    }
                    print(
                        f"warning: ka10037 HTTP 429 at market {market_name}, "
                        f"page {page_count + 1}; skipping market",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"warning: ka10037 market {market_name} skipped: "
                        f"HTTP {error.status_code}",
                        file=sys.stderr,
                    )
                break
            except (RuntimeError, ValueError) as error:
                market_error = f"{type(error).__name__}: {error}"
                print(
                    f"warning: ka10037 market {market_name} skipped: {error}",
                    file=sys.stderr,
                )
                break

            if response.get("return_code") not in (None, 0, "0"):
                market_error = f"API failed: return_code={response.get('return_code')}"
                print(
                    f"warning: ka10037 market {market_name} skipped: "
                    f"return_code={response.get('return_code')}",
                    file=sys.stderr,
                )
                break

            raw_rows = _first(
                response,
                "frgn_wicket_trde_upper",
                "foreign_broker_trade_top",
                "output",
            )
            if not isinstance(raw_rows, list):
                market_error = "missing frgn_wicket_trde_upper list"
                print(
                    f"warning: ka10037 market {market_name} skipped: "
                    "missing frgn_wicket_trde_upper list",
                    file=sys.stderr,
                )
                break

            page_count += 1
            for row in raw_rows:
                if not isinstance(row, dict):
                    continue
                stock_code = _stock_code(row.get("stk_cd"))
                raw_value = row.get("netprps_prica")
                converted_value = _foreign_sum_eok(raw_value, divisor)
                if stock_code and converted_value is not None:
                    market_values[stock_code] = converted_value
                    if len(market_raw_samples) < 5:
                        market_raw_samples.append(
                            {
                                "stock_code": stock_code,
                                "netprps_prica": raw_value,
                            }
                        )
                        market_converted_samples.append(
                            {
                                "stock_code": stock_code,
                                "foreign_sum": converted_value,
                            }
                        )

            continuation = response_headers.get("cont-yn", "").upper()
            next_key = response_headers.get("next-key", "")
            if continuation != "Y" or not next_key:
                break
            if next_key in seen_next_keys:
                market_error = f"repeated next-key: {next_key}"
                print(
                    f"warning: ka10037 market {market_name} skipped: "
                    f"repeated next-key {next_key}",
                    file=sys.stderr,
                )
                break
            seen_next_keys.add(next_key)

        if market_error is None:
            foreign_sum_by_code.update(market_values)
            stats["market_counts"][market_name] = len(market_values)
            remaining_sample_count = 5 - len(stats["raw_samples"])
            if remaining_sample_count > 0:
                stats["raw_samples"].extend(
                    market_raw_samples[:remaining_sample_count]
                )
                stats["converted_samples"].extend(
                    market_converted_samples[:remaining_sample_count]
                )
        else:
            stats["errors"].append(
                {"market": market_name, "error": market_error}
            )

    return {"values": foreign_sum_by_code, "stats": stats}


def fetch_foreign_investor_net_after_close(access_token, query_date):
    """Fetch ka10066 after-close foreign investor net amounts by stock."""
    sleep_text = os.getenv(
        "KIWOOM_FOREIGN_INVESTOR_NET_REQUEST_SLEEP_SEC", "0.25"
    )
    try:
        sleep_seconds = Decimal(sleep_text)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_FOREIGN_INVESTOR_NET_REQUEST_SLEEP_SEC must be a "
            "non-negative number"
        ) from error
    if not sleep_seconds.is_finite() or sleep_seconds < 0:
        raise RuntimeError(
            "KIWOOM_FOREIGN_INVESTOR_NET_REQUEST_SLEEP_SEC must be a "
            "non-negative number"
        )
    sleep_seconds = float(sleep_seconds)
    values = {}
    stats = {
        "available": False,
        "query_date": query_date,
        "market_counts": {"KOSPI": 0, "KOSDAQ": 0},
        "market_stats": [],
        "collected_count": 0,
        "joined_count": 0,
        "errors": [],
        "rate_limit": None,
        "raw_samples": [],
        "converted_samples": [],
        "request_sleep_seconds": sleep_seconds,
    }

    for market_name, market_type in (("KOSPI", "001"), ("KOSDAQ", "101")):
        continuation = "N"
        next_key = ""
        seen_next_keys = set()
        page_count = 0
        row_count = 0
        market_values = {}
        market_raw_samples = []
        market_converted_samples = []
        market_error = None
        market_rate_limited = False

        while True:
            try:
                response, response_headers = _post_json(
                    "/api/dostk/mrkcond",
                    {
                        "mrkt_tp": market_type,
                        "amt_qty_tp": "1",
                        "trde_tp": "0",
                        "stex_tp": "3",
                    },
                    {
                        "Authorization": f"Bearer {access_token}",
                        "api-id": "ka10066",
                        "cont-yn": continuation,
                        "next-key": next_key,
                    },
                    return_headers=True,
                )
            except KiwoomAPIError as error:
                market_error = f"HTTP {error.status_code}: {error.detail}"
                if error.status_code == 429:
                    market_rate_limited = True
                    if stats["rate_limit"] is None:
                        stats["rate_limit"] = {
                            "market": market_name,
                            "page": page_count + 1,
                        }
                    print(
                        f"warning: ka10066 HTTP 429 at market {market_name}, "
                        f"page {page_count + 1}; keeping partial market result",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"warning: ka10066 market {market_name} skipped: "
                        f"HTTP {error.status_code}",
                        file=sys.stderr,
                    )
                break
            except (RuntimeError, ValueError) as error:
                market_error = f"{type(error).__name__}: {error}"
                print(
                    f"warning: ka10066 market {market_name} skipped: {error}",
                    file=sys.stderr,
                )
                break

            if response.get("return_code") not in (None, 0, "0"):
                market_error = (
                    f"API failed: return_code={response.get('return_code')}"
                )
                print(
                    f"warning: ka10066 market {market_name} skipped: "
                    f"return_code={response.get('return_code')}",
                    file=sys.stderr,
                )
                break

            raw_rows = _first(
                response,
                "opaf_invsr_trde",
                "after_close_investor_trade",
                "output",
            )
            if not isinstance(raw_rows, list):
                market_error = "missing opaf_invsr_trde list"
                print(
                    f"warning: ka10066 market {market_name} skipped: "
                    "missing opaf_invsr_trde list",
                    file=sys.stderr,
                )
                break

            page_count += 1
            page_rows = [row for row in raw_rows if isinstance(row, dict)]
            row_count += len(page_rows)
            for row in page_rows:
                stock_code = _stock_code(row.get("stk_cd"))
                raw_value = row.get("frgnr_invsr")
                converted_value = _foreign_sum_eok(raw_value, Decimal("100"))
                if stock_code and converted_value is not None:
                    market_values[stock_code] = converted_value
                    if len(market_raw_samples) < 5:
                        market_raw_samples.append(
                            {
                                "stock_code": stock_code,
                                "frgnr_invsr": raw_value,
                            }
                        )
                        market_converted_samples.append(
                            {
                                "stock_code": stock_code,
                                "foreign_investor_net": converted_value,
                            }
                        )

            continuation = response_headers.get("cont-yn", "").upper()
            next_key = response_headers.get("next-key", "")
            if continuation != "Y" or not next_key:
                break
            if next_key in seen_next_keys:
                market_error = f"repeated next-key: {next_key}"
                print(
                    f"warning: ka10066 market {market_name} skipped: "
                    f"repeated next-key {next_key}",
                    file=sys.stderr,
                )
                break
            seen_next_keys.add(next_key)
            if sleep_seconds:
                time.sleep(sleep_seconds)

        if market_error is None or market_rate_limited:
            values.update(market_values)
            stats["market_counts"][market_name] = len(market_values)
            remaining_sample_count = 5 - len(stats["raw_samples"])
            if remaining_sample_count > 0:
                stats["raw_samples"].extend(
                    market_raw_samples[:remaining_sample_count]
                )
                stats["converted_samples"].extend(
                    market_converted_samples[:remaining_sample_count]
                )
        else:
            stats["errors"].append(
                {"market": market_name, "error": market_error}
            )

        stats["market_stats"].append(
            {
                "market": market_name,
                "pages": page_count,
                "rows": row_count,
                "count": stats["market_counts"][market_name],
                "error": market_error,
                "rate_limited": market_rate_limited,
            }
        )
        if market_rate_limited:
            time.sleep(max(1.0, sleep_seconds))

    stats["available"] = any(
        market["error"] is None or market["rate_limited"]
        for market in stats["market_stats"]
    )
    stats["collected_count"] = len(values)
    return {
        "values": values,
        "stats": stats,
    }


def _fetch_market_investor_flow(access_token, market_name, market_type, base_date):
    response = _post_json(
        "/api/dostk/sect",
        {
            "mrkt_tp": market_type,
            "amt_qty_tp": "0",
            "base_dt": base_date,
            "stex_tp": "3",
        },
        {
            "Authorization": f"Bearer {access_token}",
            "api-id": "ka10051",
            "cont-yn": "N",
            "next-key": "",
        },
    )
    if response.get("return_code") not in (None, 0, "0"):
        raise RuntimeError(f"ka10051 {market_name} failed: {response}")
    rows = response.get("inds_netprps", [])
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected ka10051 {market_name} response: {response}")
    aggregate = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and (
                "\uc885\ud569" in str(row.get("inds_nm", ""))
                or str(row.get("inds_cd", "")).split("_", 1)[0]
                == ("001" if market_type == "0" else "101")
            )
        ),
        None,
    )
    if aggregate is None:
        return None
    # ka10051 amount mode exposes the HTS market aggregate in eok units.
    return {
        "individual_eok": _market_flow_number(
            aggregate.get("ind_netprps"), "ind_netprps", market_name
        ),
        "foreign_spot_eok": _market_flow_number(
            aggregate.get("frgnr_netprps"), "frgnr_netprps", market_name
        ),
        "institution_eok": _market_flow_number(
            aggregate.get("orgn_netprps"), "orgn_netprps", market_name
        ),
    }


def _fetch_market_program_flow(access_token, market_name, market_type, base_date):
    response = _post_json(
        "/api/dostk/mrkcond",
        {
            "date": base_date,
            "amt_qty_tp": "1",
            "mrkt_tp": market_type,
            "min_tic_tp": "1",
            "stex_tp": "3",
        },
        {
            "Authorization": f"Bearer {access_token}",
            "api-id": "ka90005",
            "cont-yn": "N",
            "next-key": "",
        },
    )
    if response.get("return_code") not in (None, 0, "0"):
        raise RuntimeError(f"ka90005 {market_name} failed: {response}")
    rows = response.get("prm_trde_trnsn", [])
    if rows is None:
        return None
    if not isinstance(rows, list):
        raise RuntimeError(f"Unexpected ka90005 {market_name} response: {response}")
    latest = max(
        (row for row in rows if isinstance(row, dict) and row.get("cntr_tm")),
        key=lambda row: str(row["cntr_tm"]),
        default=None,
    )
    if latest is None:
        return None
    return _million_to_eok(latest.get("all_netprps"), "all_netprps", market_name)


def _empty_market_supply_entry(market_name):
    return {
        "market_name": market_name,
        "market_index": None,
        "market_change_rate": None,
        "advancers": None,
        "upper_limit_count": None,
        "decliners": None,
        "lower_limit_count": None,
        "individual_eok": None,
        # Kiwoom REST does not expose these two HTS-wide common values.
        "foreign_futures_eok": None,
        "foreign_spot_eok": None,
        "institution_eok": None,
        "program_market_eok": None,
        "available": False,
        "status": "unavailable",
        "error": None,
    }


def fetch_market_supply(access_token, query_date):
    """Fetch raw KOSPI/KOSDAQ index and advance/decline statistics."""
    market_supply = {}
    errors = []
    markets = (
        ("kospi", "KOSPI", "0", "001", "P001_AL01"),
        ("kosdaq", "KOSDAQ", "1", "101", "P101_AL02"),
    )
    for key, market_name, market_type, industry_code, _ in markets:
        entry = _empty_market_supply_entry(market_name)
        market_supply[key] = entry
        try:
            response = _post_json(
                "/api/dostk/sect",
                {"mrkt_tp": market_type, "inds_cd": industry_code},
                {
                    "Authorization": f"Bearer {access_token}",
                    "api-id": "ka20001",
                    "cont-yn": "N",
                    "next-key": "",
                },
            )
            return_code = response.get("return_code")
            if return_code not in (None, 0, "0"):
                raise RuntimeError(f"ka20001 {market_name} failed: {response}")

            entry.update(
                {
                    # Kiwoom signs cur_prc by direction; the displayed index is its magnitude.
                    "market_index": abs(
                        _required_market_number(response, "cur_prc", market_name)
                    ),
                    "market_change_rate": _required_market_number(
                        response, "flu_rt", market_name
                    ),
                    "advancers": _required_market_count(
                        response, "rising", market_name
                    ),
                    "upper_limit_count": _required_market_count(
                        response, "upl", market_name
                    ),
                    "decliners": _required_market_count(
                        response, "fall", market_name
                    ),
                    "lower_limit_count": _required_market_count(
                        response, "lst", market_name
                    ),
                }
            )
        except (KiwoomAPIError, RuntimeError, ValueError) as error:
            entry["error"] = str(error)
            errors.append({"market": market_name, "api": "ka20001", "error": str(error)})
            print(
                f"warning: ka20001 {market_name} market supply unavailable: {error}",
                file=sys.stderr,
            )

    for base_date in _recent_dates(query_date):
        dated_flows = {}
        flow_errors = []
        for key, market_name, market_type, _, program_market_type in markets:
            investor_flow = None
            program_flow = None
            investor_failed = False
            program_failed = False
            try:
                investor_flow = _fetch_market_investor_flow(
                    access_token, market_name, market_type, base_date
                )
            except (KiwoomAPIError, RuntimeError, ValueError) as error:
                investor_failed = True
                flow_errors.append(
                    {
                        "market": market_name,
                        "api": "ka10051",
                        "date": base_date,
                        "error": str(error),
                    }
                )
            try:
                program_flow = _fetch_market_program_flow(
                    access_token, market_name, program_market_type, base_date
                )
            except (KiwoomAPIError, RuntimeError, ValueError) as error:
                program_failed = True
                flow_errors.append(
                    {
                        "market": market_name,
                        "api": "ka90005",
                        "date": base_date,
                        "error": str(error),
                    }
                )
            if investor_flow is None or program_flow is None:
                if investor_flow is None and not investor_failed:
                    flow_errors.append(
                        {
                            "market": market_name,
                            "api": "ka10051",
                            "date": base_date,
                            "error": "no aggregate market flow row",
                        }
                    )
                if program_flow is None and not program_failed:
                    flow_errors.append(
                        {
                            "market": market_name,
                            "api": "ka90005",
                            "date": base_date,
                            "error": "no program market flow row",
                        }
                    )
                continue
            dated_flows[key] = {
                **investor_flow,
                "program_market_eok": program_flow,
            }
        if len(dated_flows) == len(markets):
            for key, values in dated_flows.items():
                market_supply[key].update(
                    {
                        **values,
                        "available": True,
                        "status": "available",
                        "error": None,
                        "flow_date": base_date,
                    }
                )
            market_supply["_status"] = {
                "available": True,
                "status": "available",
                "error": None,
                "query_date": query_date,
                "flow_date": base_date,
                "errors": errors,
            }
            return market_supply
        errors.extend(flow_errors)

    error_message = (
        f"ka10051/ka90005 market flow data unavailable through {query_date}"
    )
    for entry in market_supply.values():
        if entry["error"] is None:
            entry["error"] = error_message
    market_supply["_status"] = {
        "available": False,
        "status": "unavailable",
        "error": error_message,
        "query_date": query_date,
        "flow_date": None,
        "errors": errors,
    }
    print(f"warning: {error_message}", file=sys.stderr)
    return market_supply


def fetch_ohlc(access_token, rows, query_date, sleep_seconds):
    """Attach actual ka10086 OHLC data to every displayed row."""
    raw_samples = []
    converted_samples = []
    vwap_samples = []
    failed_samples = []
    failed_count = 0
    joined_count = 0
    rate_limit = None
    target_rows = rows

    def record_failure(position, stock_code, reason):
        nonlocal failed_count
        failed_count += 1
        if len(failed_samples) < 5:
            failed_samples.append(
                {
                    "position": position,
                    "stock_code": stock_code,
                    "reason": reason,
                }
            )

    def request_ohlc(position, stock_code):
        rate_limited = False
        failure_reason = None
        response = None

        for attempt in range(1, 4):
            try:
                response = _post_json(
                    "/api/dostk/mrkcond",
                    {
                        "stk_cd": stock_code,
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
                break
            except KiwoomAPIError as error:
                if error.status_code != 429:
                    failure_reason = f"HTTP {error.status_code}"
                    break
                rate_limited = True
                if attempt == 3:
                    failure_reason = "HTTP 429 after 3 attempts"
                    break
                retry_delay = max(1.0, sleep_seconds)
                print(
                    f"warning: ka10086 HTTP 429 at position {position}, "
                    f"stock_code {stock_code}; retry {attempt}/2 after "
                    f"{retry_delay:g}s",
                    file=sys.stderr,
                )
                time.sleep(retry_delay)
            except (RuntimeError, ValueError) as error:
                failure_reason = f"{type(error).__name__}: {error}"
                break

        return response, failure_reason, rate_limited

    pending_requests = []
    worker_count = min(4, len(target_rows)) or 1
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for position, row in enumerate(target_rows, start=1):
            row["ohlc"] = None
            pending_requests.append(
                (
                    position,
                    row,
                    executor.submit(request_ohlc, position, row["stock_code"]),
                )
            )
            if position < len(target_rows) and sleep_seconds:
                time.sleep(sleep_seconds)

        for position, row, request in pending_requests:
            stock_code = row["stock_code"]
            response, failure_reason, was_rate_limited = request.result()
            if was_rate_limited and rate_limit is None:
                rate_limit = {"position": position, "stock_code": stock_code}
            if response is None:
                record_failure(position, stock_code, failure_reason or "request failed")
                continue

            daily_rows = _first(response, "daly_stkpc", "daily_stock_price", "output")
            if not isinstance(daily_rows, list):
                return_code = _first(response, "return_code", "code")
                return_message = _first(response, "return_msg", "message")
                record_failure(
                    position,
                    stock_code,
                    f"unexpected response data: code={return_code}, "
                    f"message={return_message}",
                )
                continue
            current_row, previous_row = _select_daily_rows(daily_rows, query_date)
            trading_date = _daily_row_date(current_row) if current_row else None
            if trading_date and trading_date != query_date:
                print(
                    "ka10086 trading_date_used: "
                    f"stock_code={stock_code}, query_date={query_date}, "
                    f"trading_date={trading_date}"
                )
            ohlc, vwap_sample = _build_ohlc(current_row, previous_row)
            if len(raw_samples) < 5:
                raw_samples.append(
                    {
                        "stock_code": stock_code,
                        "current": _ohlc_row_sample(current_row),
                        "previous": _ohlc_row_sample(previous_row),
                    }
                )
                converted_samples.append({"stock_code": stock_code, "ohlc": ohlc})
                if vwap_sample is not None:
                    vwap_samples.append({"stock_code": stock_code, **vwap_sample})
            if ohlc is not None:
                row["ohlc"] = ohlc
                joined_count += 1
            else:
                record_failure(position, stock_code, "missing or invalid daily data")

    attached_count = sum(row.get("ohlc") is not None for row in rows)
    if attached_count != joined_count:
        raise RuntimeError(
            "ka10086 joined count does not match fetch_ohlc rows: "
            f"joined={joined_count}, rows={attached_count}"
        )
    print(
        f"ka10086 OHLC attached before return: rows={len(rows)}, "
        f"ohlc={attached_count}, rows_id={id(rows)}, pid={os.getpid()}",
        flush=True,
    )

    return {
        "target_count": len(target_rows),
        "joined_count": joined_count,
        "attached_count": attached_count,
        "rows_id": id(rows),
        "failed_count": failed_count,
        "failed_samples": failed_samples,
        "raw_samples": raw_samples,
        "converted_samples": converted_samples,
        "vwap_samples": vwap_samples,
        "rate_limit": rate_limit,
    }


class KiwoomOpenApiRealtimeProvider:
    """Optional OpenAPI+ lifecycle skeleton without connection or FID parsing."""

    _REALREG_BATCH_SIZE = 100
    _REALREG_SCREEN_START = 9000
    _REALREG_FIDS = "10;12;20;15;228;13;14;290"
    _REALREG_REAL_TYPE = "\uc8fc\uc2dd\uccb4\uacb0"
    _ORDERBOOK_REALREG_SCREEN_START = 9010
    _ORDERBOOK_HOT_SCREEN = "9010"
    _ORDERBOOK_ROTATE_SCREEN = "9011"
    _STRENGTH_PROBE_SCREEN = "9020"
    _STRENGTH_PROBE_RQNAME = "stockboard_opt10046_probe"
    _STRENGTH_PROBE_TRCODE = "opt10046"
    _STRENGTH_PROBE_FIELDS = (
        "\uccb4\uacb0\uac15\ub3c4",
        "\uccb4\uacb0\uac15\ub3c45\ubd84",
        "\uccb4\uacb0\uac15\ub3c420\ubd84",
        "\uccb4\uacb0\uac15\ub3c460\ubd84",
    )
    _ORDERBOOK_PROBE_SCREEN = "9021"
    _ORDERBOOK_PROBE_RQNAME = "stockboard_opt10004_probe"
    _ORDERBOOK_PROBE_TRCODE = "opt10004"
    _OPT10055_PROBE_SCREEN = "9022"
    _OPT10055_PROBE_RQNAME = "stockboard_opt10055_probe"
    _OPT10055_PROBE_TRCODE = "OPT10055"
    _OPT10055_PROBE_FIELDS = (
        "\uccb4\uacb0\uc2dc\uac04",
        "\uccb4\uacb0\uac00",
        "\uc804\uc77c\ub300\ube44\uae30\ud638",
        "\uc804\uc77c\ub300\ube44",
        "\ub4f1\ub77d\ub960",
        "\uccb4\uacb0\ub7c9",
        "\ub204\uc801\uac70\ub798\ub7c9",
        "\ub204\uc801\uac70\ub798\ub300\uae08",
    )
    _ORDERBOOK_PROBE_TOTAL_ASK_FIELDS = (
        "\ucd1d\ub9e4\ub3c4\uc794\ub7c9",
        "\ub9e4\ub3c4\ud638\uac00\ucd1d\uc794\ub7c9",
        "\ub9e4\ub3c4\uc794\ub7c9",
    )
    _ORDERBOOK_PROBE_TOTAL_BID_FIELDS = (
        "\ucd1d\ub9e4\uc218\uc794\ub7c9",
        "\ub9e4\uc218\ud638\uac00\ucd1d\uc794\ub7c9",
        "\ub9e4\uc218\uc794\ub7c9",
    )
    _ORDERBOOK_PROBE_LEVEL_ASK_FIELDS = tuple(
        [f"\ub9e4\ub3c4\uc794\ub7c9{index}" for index in range(1, 11)]
        + [f"\ub9e4\ub3c4\ud638\uac00\uc794\ub7c9{index}" for index in range(1, 11)]
    )
    _ORDERBOOK_PROBE_LEVEL_BID_FIELDS = tuple(
        [f"\ub9e4\uc218\uc794\ub7c9{index}" for index in range(1, 11)]
        + [f"\ub9e4\uc218\ud638\uac00\uc794\ub7c9{index}" for index in range(1, 11)]
    )
    _ORDERBOOK_PROBE_PRICE_FIELDS = tuple(
        [f"\ub9e4\ub3c4\ud638\uac00{index}" for index in range(1, 11)]
        + [f"\ub9e4\uc218\ud638\uac00{index}" for index in range(1, 11)]
    )
    _ORDERBOOK_REALREG_FIDS = "41;51;121;125"
    _ECN_ORDERBOOK_REAL_TYPE = "ECN\uc8fc\uc2dd\ud638\uac00\uc794\ub7c9"
    _EXPECTED_REALREG_SCREEN_START = 9020
    _EXPECTED_REALREG_FIDS = "10;12;20;15;13;14"
    _EXPECTED_REAL_TYPE = "\uc8fc\uc2dd\uc608\uc0c1\uccb4\uacb0"
    _AFTER_SINGLE_REALREG_SCREEN_START = 9030
    _AFTER_SINGLE_REALREG_FIDS = "10;12;20;15;13;14"
    _AFTER_SINGLE_REAL_TYPE = "\uc2dc\uac04\uc678\ub2e8\uc77c\uac00"
    _SUFFIX_REALREG_FIDS = "10;12;20;15;13;14;290"
    _SUFFIX_REALREG_GROUPS = (
        ("KRX", "9100", ("005930", "000660", "402340")),
        ("NX", "9120", ("005930_NX", "000660_NX", "402340_NX")),
    )
    _SUFFIX_OPERATING_AL_CODES = ("005930_AL", "000660_AL", "402340_AL")
    _ORDERBOOK_REAL_TYPE = "\uc8fc\uc2dd\ud638\uac00\uc794\ub7c9"
    _TRADE_REALDATA_FIDS = (
        (10, "price_raw"),
        (12, "change_rate_raw"),
        (20, "trade_time_raw"),
        (15, "trade_qty_raw"),
        (228, "execution_strength_raw"),
        (13, "cumulative_volume_raw"),
        (14, "cumulative_value_raw"),
        (290, "market_type_raw"),
    )
    _DIAGNOSTIC_REALDATA_FIDS = (
        (10, "price_raw"),
        (12, "change_rate_raw"),
        (20, "trade_time_raw"),
        (15, "trade_qty_raw"),
        (13, "cumulative_volume_raw"),
        (14, "cumulative_value_raw"),
    )
    _ORDERBOOK_REALDATA_FIDS = (
        (41, "best_ask_price_raw"),
        (51, "best_bid_price_raw"),
        (121, "ask_volume_raw"),
        (125, "bid_volume_raw"),
    )
    _ECN_ORDERBOOK_REALDATA_FIDS = (
        (10, "fid10_raw"),
        (12, "fid12_raw"),
        (20, "fid20_raw"),
        (13, "fid13_raw"),
        (14, "fid14_raw"),
        (15, "fid15_raw"),
        (27, "fid27_raw"),
        (28, "fid28_raw"),
        (41, "fid41_raw"),
        (51, "fid51_raw"),
        (121, "fid121_raw"),
        (125, "fid125_raw"),
        (290, "fid290_raw"),
    )

    _SUFFIX_REALDATA_FIDS = (
        (10, "price_raw"),
        (12, "change_rate_raw"),
        (20, "trade_time_raw"),
        (15, "trade_qty_raw"),
        (13, "cumulative_volume_raw"),
        (14, "cumulative_value_raw"),
        (290, "fid290_raw"),
    )

    def __init__(self, store=None, logger=None):
        self.store = store
        self.logger = logger
        self._lock = RLock()
        self._availability_checked = False
        self._available = False
        self._backend = None
        self._running = False
        self._registered_codes = set()
        self._registered_code_to_normalized = {}
        self._last_error = None
        self._last_received_at = None
        self._app = None
        self._control = None
        self._qt_thread = None
        self._owns_app = False
        self._original_registered_codes = {}
        self._qt_ready = False
        self._control_created = False
        self._tr_event_connected = False
        self._login_requested = False
        self._login_state = "not_requested"
        self._login_error_code = None
        self._login_completed_at = None
        self._qt_pump_thread = None
        self._qt_pump_stop_event = None
        self._qt_ready_event = None
        self._qt_pump_running = False
        self._qt_pump_last_at = None
        self._pending_register_codes = None
        self._pending_unregister = False
        self._pending_orderbook_hot_refresh = False
        self._orderbook_hot_refresh_requested_at = None
        self._orderbook_hot_refresh_completed_at = None
        self._orderbook_hot_refresh_error = None
        self._registered_code_order = []
        self._realreg_requested = False
        self._realreg_succeeded = False
        self._realreg_error = None
        self._realreg_screen_count = 0
        self._realreg_code_count = 0
        self._realreg_fids = self._REALREG_FIDS
        self._realreg_real_type = self._REALREG_REAL_TYPE
        self._realreg_screens = []
        self._orderbook_realreg_requested = False
        self._orderbook_realreg_succeeded = False
        self._orderbook_realreg_error = None
        self._orderbook_realreg_screen_count = 0
        self._orderbook_realreg_code_count = 0
        self._orderbook_realreg_fids = self._ORDERBOOK_REALREG_FIDS
        self._orderbook_realreg_real_type = self._ORDERBOOK_REAL_TYPE
        self._orderbook_realreg_screens = []
        self._expected_realreg_succeeded = False
        self._expected_realreg_error = None
        self._expected_realreg_screen_count = 0
        self._expected_realreg_code_count = 0
        self._expected_realreg_fids = self._EXPECTED_REALREG_FIDS
        self._expected_realreg_real_type = self._EXPECTED_REAL_TYPE
        self._expected_realreg_screens = []
        self._after_single_realreg_succeeded = False
        self._after_single_realreg_error = None
        self._after_single_realreg_screen_count = 0
        self._after_single_realreg_code_count = 0
        self._after_single_realreg_fids = self._AFTER_SINGLE_REALREG_FIDS
        self._after_single_realreg_real_type = self._AFTER_SINGLE_REAL_TYPE
        self._after_single_realreg_screens = []
        self._suffix_realreg_requested = False
        self._suffix_realreg_succeeded = False
        self._suffix_realreg_error = None
        self._suffix_realreg_screens = []
        self._suffix_realreg_codes = []
        self._suffix_realreg_fids = self._SUFFIX_REALREG_FIDS
        self._suffix_sample_codes = set()
        self._suffix_store_skip_codes = set()
        self._suffix_last_samples = {}
        self._realdata_received_count = 0
        self._realdata_last_received_at = None
        self._realdata_last_code = None
        self._realdata_last_real_type = None
        self._realdata_last_sample = None
        self._realdata_parse_error = None
        self._last_received_code = None
        self._last_normalized_code = None
        self._last_registered_code = None
        self._last_original_registered_code = None
        self._last_fid10_raw = None
        self._last_fid20_raw = None
        self._trade_last_sample = None
        self._trade_last_received_code = None
        self._trade_last_normalized_code = None
        self._trade_last_fid10_raw = None
        self._trade_last_fid20_raw = None
        self._trade_last_received_at = None
        self._orderbook_last_sample = None
        self._orderbook_last_received_code = None
        self._orderbook_last_normalized_code = None
        self._orderbook_last_received_at = None
        self._ecn_orderbook_last_sample = None
        self._ecn_orderbook_last_received_code = None
        self._ecn_orderbook_last_normalized_code = None
        self._ecn_orderbook_last_received_at = None
        self._ecn_orderbook_seen_codes = set()
        self._expected_last_sample = None
        self._expected_last_received_code = None
        self._expected_last_received_at = None
        self._after_single_last_sample = None
        self._after_single_last_received_code = None
        self._after_single_last_received_at = None
        self._trade_fid290_raw = None
        self._trade_seen_codes = set()
        self._orderbook_seen_codes = set()
        self._register_input_codes_sample = []
        self._register_normalized_codes_sample = []
        self._setrealreg_codes_sample = []
        self._register_code_map_sample = []
        self._unregister_requested = False
        self._unregister_succeeded = False
        self._unregister_error = None
        self._price_fast_mode = _env_bool("STOCKBOARD_PRICE_FAST_MODE", False)
        self._price_light_lane_enabled = _env_bool(
            "STOCKBOARD_PRICE_LIGHT_LANE_ENABLED", True
        )
        self._price_light_top_limit = max(
            0, _env_int("STOCKBOARD_PRICE_LIGHT_TOP_LIMIT", 100)
        )
        self._price_light_min_interval_sec = max(
            0.0,
            float(os.getenv("STOCKBOARD_PRICE_LIGHT_MIN_INTERVAL_SEC", "0.25")),
        )
        self._realtime_code_limit = max(
            0, _env_int("STOCKBOARD_REALTIME_CODE_LIMIT", 0)
        )
        self._orderbook_realtime_enabled = _env_bool(
            "STOCKBOARD_ENABLE_ORDERBOOK_REALTIME", True
        )
        self._display_mode = (
            os.getenv("STOCKBOARD_DISPLAY_MODE", "fast").strip().lower()
            or "fast"
        )
        self._orderbook_mode = (
            os.getenv("STOCKBOARD_ORDERBOOK_MODE", "off").strip().lower()
            or "off"
        )
        if not self._orderbook_realtime_enabled:
            self._orderbook_mode = "off"
        if self._orderbook_mode not in {"off", "hybrid", "hot_only"}:
            self._orderbook_mode = "off"
        self._orderbook_hot_source = (
            os.getenv("STOCKBOARD_ORDERBOOK_HOT_SOURCE", "top5").strip().lower()
            or "top5"
        )
        self._orderbook_hot_limit = max(
            0, _env_int("STOCKBOARD_ORDERBOOK_HOT_LIMIT", 5)
        )
        self._orderbook_rotate_batch = max(
            1, _env_int("STOCKBOARD_ORDERBOOK_ROTATE_BATCH", 20)
        )
        self._orderbook_rotate_interval_sec = max(
            1, _env_int("STOCKBOARD_ORDERBOOK_ROTATE_INTERVAL_SEC", 5)
        )
        self._orderbook_display = (
            os.getenv("STOCKBOARD_ORDERBOOK_DISPLAY", "numeric").strip().lower()
            or "numeric"
        )
        self._orderbook_hot_codes = []
        self._hot_priority_codes = []
        self._orderbook_rotate_pool = []
        self._orderbook_current_rotate_codes = []
        self._orderbook_next_rotate_index = 0
        self._orderbook_last_rotate_at = None
        self._orderbook_last_rotate_at_text = None
        self._orderbook_registered_count = 0
        self._strength_5m_enabled = _env_bool(
            "STOCKBOARD_STRENGTH_5M_ENABLED", False
        )
        self._strength_5m_queue_size = 0
        self._strength_5m_last_cycle_at = None
        self._close_metrics_queue = deque()
        self._close_metrics_queued = set()
        self._close_metrics_cache = {}
        self._close_metrics_attempts = {}
        self._close_metrics_last_request_at = 0.0
        self._close_metrics_query_interval_sec = max(
            0.34, float(os.getenv("STOCKBOARD_CLOSE_METRICS_INTERVAL_SEC", "0.34"))
        )
        self._close_metrics_max_attempts = max(
            1, _env_int("STOCKBOARD_CLOSE_METRICS_MAX_ATTEMPTS", 1)
        )
        self._close_metrics_last_cycle_at = None
        self._close_metrics_last_error = None
        self._close_metrics_tr_notes = {
            "strength": (
                "opt10046 strength queue enabled for active/candidate/top20; "
                "provider-ready gated"
            ),
            "orderbook": (
                "opt10004 orderbook snapshot confirmed for total bid/ask "
                "volume; active/candidate/top20 queue enabled"
            ),
        }
        self._strength_probe_pending = deque()
        self._strength_probe_pending_codes = set()
        self._strength_probe_inflight = None
        self._strength_probe_cache = {}
        self._strength_probe_last_request_at = 0.0
        self._strength_probe_last_by_code = {}
        self._strength_probe_last_result = None
        self._strength_probe_last_error = None
        self._strength_probe_last_raw_sample = []
        self._strength_probe_min_interval_sec = 1.0
        self._strength_probe_duplicate_window_sec = 30.0
        self._strength_probe_timeout_sec = 10.0
        self._strength_probe_not_ready_retry_sec = 5.0
        self._strength_probe_not_ready_reason = "not_checked"
        self._orderbook_probe_pending = deque()
        self._orderbook_probe_pending_codes = set()
        self._orderbook_probe_inflight = None
        self._orderbook_probe_cache = {}
        self._orderbook_probe_last_request_at = 0.0
        self._orderbook_probe_last_by_code = {}
        self._orderbook_probe_last_result = None
        self._orderbook_probe_last_error = None
        self._orderbook_probe_last_raw_sample = []
        self._orderbook_probe_min_interval_sec = 1.0
        self._orderbook_probe_duplicate_window_sec = 30.0
        self._orderbook_probe_timeout_sec = 10.0
        self._orderbook_probe_not_ready_retry_sec = 5.0
        self._orderbook_probe_not_ready_reason = "not_checked"
        self._opt10055_probe_pending = deque()
        self._opt10055_probe_pending_keys = set()
        self._opt10055_probe_inflight = None
        self._opt10055_probe_cache = {}
        self._opt10055_probe_last_request_at = 0.0
        self._opt10055_probe_last_result = None
        self._opt10055_probe_last_error = None
        self._opt10055_probe_last_raw_sample = []
        self._opt10055_probe_min_interval_sec = 1.0
        self._opt10055_probe_timeout_sec = 10.0
        self._opt10055_probe_not_ready_retry_sec = 5.0
        self._opt10055_probe_not_ready_reason = "not_checked"
        self._stale_trade_drop_seconds = max(
            0, _env_int("STOCKBOARD_DROP_STALE_TRADE_SECONDS", 0)
        )
        self._stale_trade_drop_count = 0
        self._stale_trade_suspect_count = 0
        self._older_trade_drop_count = 0
        self._latest_only_enabled = True
        self._latest_only_dropped_count = 0
        self._trade_event_received_count = 0
        self._trade_event_applied_count = 0
        self._last_stale_trade_lag_sec = None
        self._last_stale_trade_suspect_lag_sec = None
        self._last_trade_lag_sec = None
        self._max_trade_lag_sec = None
        self._recent_trade_lags = deque(maxlen=200)
        self._latest_trade_lag_by_code = {}
        self._last_accepted_trade_time_by_code = {}

    def _check_availability(self):
        if self._availability_checked:
            return self._available
        self._availability_checked = True
        errors = []
        try:
            from PyQt5.QAxContainer import QAxWidget  # noqa: F401

            self._backend = "PyQt5.QAxContainer"
            self._available = True
        except Exception as error:
            errors.append(f"PyQt5.QAxContainer: {error}")

        if not self._available:
            try:
                import win32com.client  # noqa: F401

                self._backend = "win32com.client"
                self._available = True
            except Exception as error:
                errors.append(f"win32com.client: {error}")

        self._last_error = None if self._available else "; ".join(errors)
        return self._available

    def start(self):
        with self._lock:
            if self._running:
                return True
            if not self._check_availability():
                self._running = False
                return False
            self._qt_pump_stop_event = Event()
            self._qt_ready_event = Event()
            self._qt_pump_thread = Thread(
                target=self._pump_qt_events,
                name="StockBoardQtEventPump",
                daemon=True,
            )
            ready_event = self._qt_ready_event
            self._qt_pump_thread.start()

        if not ready_event.wait(timeout=10.0):
            with self._lock:
                self._last_error = "QAxWidget initialization timed out"
                self._running = False
            self._stop_qt_pump()
            return False

        with self._lock:
            return self._running

    def stop(self):
        return self._stop_qt_pump()

    def _stop_qt_pump(self):
        with self._lock:
            stop_event = self._qt_pump_stop_event
            pump_thread = self._qt_pump_thread
        if stop_event is not None:
            stop_event.set()
        if pump_thread is not None and pump_thread.is_alive():
            pump_thread.join(timeout=2.0)
        stopped = pump_thread is None or not pump_thread.is_alive()
        with self._lock:
            if not stopped:
                self._last_error = "Qt event pump did not stop within timeout"
            self._qt_pump_running = False
            if stopped:
                self._qt_pump_thread = None
                self._qt_pump_stop_event = None
                self._qt_ready_event = None
            self._running = False
        return stopped

    def _pump_qt_events(self):
        app = None
        control = None
        owns_app = False
        try:
            from PyQt5.QtCore import QCoreApplication, QThread
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QAxContainer import QAxWidget

            app = QCoreApplication.instance()
            if app is None:
                app = QApplication([])
                owns_app = True
            elif not isinstance(app, QApplication):
                raise RuntimeError(
                    "QAxWidget requires QApplication, but a QCoreApplication "
                    "instance already exists"
                )

            with self._lock:
                self._app = app
                self._qt_thread = QThread.currentThread()
                self._owns_app = owns_app
                self._qt_ready = True
                self._qt_pump_running = True

            control = QAxWidget("KHOPENAPI.KHOpenAPICtrl.1")
            if control.isNull():
                raise RuntimeError(
                    "failed to create KHOPENAPI.KHOpenAPICtrl.1 QAx control"
                )
            control.OnEventConnect.connect(self._on_event_connect)
            control.OnReceiveRealData.connect(self._on_receive_real_data)
            control.OnReceiveTrData.connect(self._on_receive_tr_data)

            with self._lock:
                self._control = control
                self._control_created = True
                self._tr_event_connected = True
                self._running = True

            login_request_succeeded = self._request_login()
            if login_request_succeeded:
                with self._lock:
                    self._last_error = None

            with self._lock:
                ready_event = self._qt_ready_event
            if ready_event is not None:
                ready_event.set()

            while True:
                with self._lock:
                    app = self._app
                    stop_event = self._qt_pump_stop_event
                if app is None or stop_event is None or stop_event.is_set():
                    break
                try:
                    app.processEvents()
                    self._process_pending_realtime_requests()
                    self._process_orderbook_rotation()
                    self._process_strength_probe_queue()
                    self._process_orderbook_probe_queue()
                    self._process_opt10055_probe_queue()
                    self._process_close_metrics_queue()
                    pump_time = datetime.now().isoformat(timespec="seconds")
                    with self._lock:
                        self._qt_pump_running = True
                        self._qt_pump_last_at = pump_time
                except Exception as error:
                    with self._lock:
                        self._last_error = f"Qt event pump failed: {error}"
                        self._qt_pump_running = False
                    break
                time.sleep(0.02)
        except Exception as error:
            with self._lock:
                self._last_error = f"QAxWidget initialization failed: {error}"
                self._running = False
                self._qt_ready = False
                self._control_created = False
                ready_event = self._qt_ready_event
            if ready_event is not None:
                ready_event.set()
        finally:
            cleanup_error = None
            try:
                if control is not None:
                    control.clear()
                    control.deleteLater()
                if app is not None:
                    app.processEvents()
                    if owns_app:
                        app.quit()
            except Exception as error:
                cleanup_error = error
            with self._lock:
                if cleanup_error is not None:
                    self._last_error = f"QAxWidget cleanup failed: {cleanup_error}"
                self._qt_pump_running = False
                self._control = None
                self._app = None
                self._qt_thread = None
                self._owns_app = False
                self._qt_ready = False
                self._control_created = False
                self._running = False

    def _request_login(self):
        if self._login_requested:
            return True
        self._login_requested = True
        self._login_state = "requested"
        self._login_error_code = None
        self._login_completed_at = None
        result = self._control.dynamicCall("CommConnect()")
        if result not in (None, 0, "0"):
            self._login_state = "failed"
            self._login_error_code = result
            self._login_completed_at = datetime.now().isoformat(timespec="seconds")
            self._last_error = f"CommConnect returned {result!r}"
            return False
        return True

    def _on_event_connect(self, err_code):
        with self._lock:
            self._login_error_code = err_code
            self._login_completed_at = datetime.now().isoformat(timespec="seconds")
            if err_code == 0:
                self._login_state = "connected"
                self._last_error = None
            else:
                self._login_state = "failed"
                self._last_error = f"OnEventConnect failed: {err_code}"

    def _registration_batches(self, codes):
        for index in range(0, len(codes), self._REALREG_BATCH_SIZE):
            screen = str(self._REALREG_SCREEN_START + index // self._REALREG_BATCH_SIZE)
            yield screen, codes[index : index + self._REALREG_BATCH_SIZE]

    def _orderbook_registration_batches(self, codes):
        for index in range(0, len(codes), self._REALREG_BATCH_SIZE):
            screen = str(
                self._ORDERBOOK_REALREG_SCREEN_START
                + index // self._REALREG_BATCH_SIZE
            )
            yield screen, codes[index : index + self._REALREG_BATCH_SIZE]

    def _orderbook_groups(self, codes):
        if self._orderbook_mode == "off":
            return [], []
        unique_codes = list(dict.fromkeys(codes))
        priority_codes = [
            code
            for code in self._hot_priority_codes
            if code in set(unique_codes)
        ]
        hot_candidates = list(dict.fromkeys(priority_codes + unique_codes))
        hot_limit = max(self._orderbook_hot_limit, len(priority_codes))
        hot_codes = hot_candidates[:hot_limit]
        if self._orderbook_mode == "hot_only":
            return hot_codes, []
        rotate_pool = [
            code for code in unique_codes if code not in set(hot_codes)
        ]
        return hot_codes, rotate_pool

    def set_hot_priority_codes(self, codes):
        with self._lock:
            registered_code_to_normalized = dict(
                self._registered_code_to_normalized
            )
        normalized_to_registered = {}
        for registered, normalized in registered_code_to_normalized.items():
            normalized_to_registered.setdefault(normalized, registered)
        priority_codes = []
        for code in codes or []:
            normalized_code = _stock_code(code)
            if normalized_code is None:
                continue
            register_code = normalized_to_registered.get(normalized_code)
            priority_codes.append(register_code or f"{normalized_code}_AL")
        priority_codes = list(dict.fromkeys(priority_codes))
        with self._lock:
            changed = priority_codes != self._hot_priority_codes
            self._hot_priority_codes = priority_codes
            if changed and self._orderbook_mode != "off":
                self._pending_orderbook_hot_refresh = True
                self._orderbook_hot_refresh_requested_at = (
                    datetime.now().isoformat(timespec="seconds")
                )
                self._orderbook_hot_refresh_error = None
        return priority_codes

    def _next_orderbook_rotate_batch(self):
        pool = list(self._orderbook_rotate_pool)
        if not pool:
            return []
        start = self._orderbook_next_rotate_index % len(pool)
        batch = []
        for offset in range(min(self._orderbook_rotate_batch, len(pool))):
            batch.append(pool[(start + offset) % len(pool)])
        self._orderbook_next_rotate_index = (
            start + len(batch)
        ) % len(pool)
        return batch

    def _disconnect_realdata_screen(self, screen):
        with self._lock:
            control = self._control
        if control is None:
            return
        control.dynamicCall("DisconnectRealData(QString)", screen)

    def _register_orderbook_screen(self, screen, codes):
        if not codes:
            return False
        with self._lock:
            control = self._control
        if control is None:
            raise RuntimeError("QAx control is not available")
        result = control.dynamicCall(
            "SetRealReg(QString, QString, QString, QString)",
            screen,
            ";".join(codes),
            self._ORDERBOOK_REALREG_FIDS,
            "0",
        )
        if not self._is_success_result(result):
            raise RuntimeError(
                f"SetRealReg orderbook screen {screen} returned {result!r}"
            )
        return True

    def _process_orderbook_rotation(self):
        if self._orderbook_mode != "hybrid":
            return
        with self._lock:
            pool = list(self._orderbook_rotate_pool)
            last_rotate_at = self._orderbook_last_rotate_at
        if not pool:
            return
        now = time.monotonic()
        if (
            last_rotate_at is not None
            and now - last_rotate_at < self._orderbook_rotate_interval_sec
        ):
            return
        with self._lock:
            batch = self._next_orderbook_rotate_batch()
        try:
            self._disconnect_realdata_screen(self._ORDERBOOK_ROTATE_SCREEN)
            if batch:
                self._register_orderbook_screen(
                    self._ORDERBOOK_ROTATE_SCREEN, batch
                )
            with self._lock:
                self._orderbook_current_rotate_codes = list(batch)
                self._orderbook_last_rotate_at = now
                self._orderbook_last_rotate_at_text = datetime.now().isoformat(
                    timespec="seconds"
                )
                self._orderbook_registered_count = len(
                    set(self._orderbook_hot_codes) | set(batch)
                )
        except Exception as error:
            with self._lock:
                self._last_error = f"orderbook rotation failed: {error}"

    def enqueue_close_metrics(self, codes, priority="background", force=False):
        normalized_codes = []
        for code in codes or []:
            normalized_code = _stock_code(code)
            if normalized_code is None:
                continue
            normalized_codes.append(normalized_code)
        if not normalized_codes:
            return {
                "accepted": 0,
                "queued": 0,
                "priority": priority,
                "strength_probe_queued": 0,
                "orderbook_probe_queued": 0,
            }
        strength_queued = 0
        strength_deferred = 0
        strength_skipped_cached = 0
        strength_already_pending = 0
        orderbook_queued = 0
        orderbook_deferred = 0
        orderbook_skipped_cached = 0
        orderbook_already_pending = 0
        for code in normalized_codes:
            response = self.enqueue_strength_probe(
                code,
                priority=priority,
                force=force,
            )
            message = response.get("message")
            status = response.get("status")
            if status == "cached":
                strength_skipped_cached += 1
            elif message == "strength probe already pending":
                strength_already_pending += 1
            elif status in {"pending", "requested"}:
                strength_queued += 1
            elif status == "deferred":
                strength_queued += 1
                strength_deferred += 1
            response = self.enqueue_orderbook_probe(
                code,
                priority=priority,
                force=force,
            )
            message = response.get("message")
            status = response.get("status")
            if status == "cached":
                orderbook_skipped_cached += 1
            elif message == "orderbook probe already pending":
                orderbook_already_pending += 1
            elif status in {"pending", "requested"}:
                orderbook_queued += 1
            elif status == "deferred":
                orderbook_queued += 1
                orderbook_deferred += 1
        return {
            "accepted": len(normalized_codes),
            "queued": strength_queued + orderbook_queued,
            "priority": priority,
            "strength_probe_queued": strength_queued,
            "strength_probe_deferred": strength_deferred,
            "strength_probe_already_pending": strength_already_pending,
            "strength_probe_skipped_cached": strength_skipped_cached,
            "orderbook_probe_queued": orderbook_queued,
            "orderbook_probe_deferred": orderbook_deferred,
            "orderbook_probe_already_pending": orderbook_already_pending,
            "orderbook_probe_skipped_cached": orderbook_skipped_cached,
            "orderbook_status": "enabled",
            "message": "strength and orderbook probes queued",
        }

    def close_metrics_status(self, codes=None):
        with self._lock:
            ready, ready_reason = self._strength_probe_ready_state_locked()
            now = time.monotonic()
            pending_items = list(self._strength_probe_pending)
            deferred_items = [
                item
                for item in pending_items
                if (item.get("next_retry_at_monotonic") or 0) > now
                or ready is not True
            ]
            orderbook_ready, orderbook_ready_reason = (
                self._orderbook_probe_ready_state_locked()
            )
            orderbook_pending_items = list(self._orderbook_probe_pending)
            orderbook_deferred_items = [
                item
                for item in orderbook_pending_items
                if (item.get("next_retry_at_monotonic") or 0) > now
                or orderbook_ready is not True
            ]
            requested_codes = [
                _stock_code(code)
                for code in (codes or [])
                if _stock_code(code) is not None
            ]
            snapshots = (
                self.store.close_metrics_snapshot(requested_codes)
                if self.store is not None
                else {}
            )
            return {
                "queue_size": len(self._close_metrics_queue),
                "queued_codes_sample": list(self._close_metrics_queue)[:20],
                "cache_size": len(self._close_metrics_cache),
                "strength_probe_cache_size": len(self._strength_probe_cache),
                "attempts_sample": dict(list(self._close_metrics_attempts.items())[:20]),
                "last_cycle_at": self._close_metrics_last_cycle_at,
                "last_error": self._close_metrics_last_error,
                "strength_probe_ready": ready,
                "provider_ready_reason": ready_reason,
                "provider_not_ready_reason": None if ready else ready_reason,
                "strength_probe_pending_count": len(self._strength_probe_pending),
                "strength_probe_pending_sample": [
                    item.get("stock_code")
                    for item in pending_items[:20]
                ],
                "strength_probe_deferred_count": len(deferred_items),
                "strength_probe_deferred_sample": [
                    item.get("stock_code")
                    for item in deferred_items[:20]
                ],
                "strength_probe_inflight_code": (
                    self._strength_probe_inflight.get("stock_code")
                    if self._strength_probe_inflight
                    else None
                ),
                "strength_probe_last_result": (
                    dict(self._strength_probe_last_result)
                    if isinstance(self._strength_probe_last_result, dict)
                    else self._strength_probe_last_result
                ),
                "strength_probe_last_error": self._strength_probe_last_error,
                "strength_probe_last_raw_sample": list(
                    self._strength_probe_last_raw_sample
                ),
                "orderbook_probe_ready": orderbook_ready,
                "orderbook_probe_ready_reason": orderbook_ready_reason,
                "orderbook_probe_not_ready_reason": (
                    None if orderbook_ready else orderbook_ready_reason
                ),
                "orderbook_probe_pending_count": len(self._orderbook_probe_pending),
                "orderbook_probe_pending_sample": [
                    item.get("stock_code")
                    for item in orderbook_pending_items[:20]
                ],
                "orderbook_probe_deferred_count": len(orderbook_deferred_items),
                "orderbook_probe_deferred_sample": [
                    item.get("stock_code")
                    for item in orderbook_deferred_items[:20]
                ],
                "orderbook_probe_inflight_code": (
                    self._orderbook_probe_inflight.get("stock_code")
                    if self._orderbook_probe_inflight
                    else None
                ),
                "orderbook_probe_cache_size": len(self._orderbook_probe_cache),
                "orderbook_probe_last_result": (
                    dict(self._orderbook_probe_last_result)
                    if isinstance(self._orderbook_probe_last_result, dict)
                    else self._orderbook_probe_last_result
                ),
                "orderbook_probe_last_error": self._orderbook_probe_last_error,
                "orderbook_probe_last_raw_sample": list(
                    self._orderbook_probe_last_raw_sample
                ),
                "rate_limit_per_sec": round(
                    1 / self._close_metrics_query_interval_sec, 3
                ),
                "tr_notes": dict(self._close_metrics_tr_notes),
                "snapshots": snapshots,
            }

    def _process_close_metrics_queue(self):
        with self._lock:
            if not self._close_metrics_queue:
                return
            now = time.monotonic()
            if now - self._close_metrics_last_request_at < (
                self._close_metrics_query_interval_sec
            ):
                return
            code = self._close_metrics_queue.popleft()
            self._close_metrics_queued.discard(code)
            self._close_metrics_last_request_at = now
        try:
            snapshot = self._query_close_metrics_snapshot(code)
            if self.store is not None:
                self.store.update_close_metrics(code, snapshot)
            with self._lock:
                self._close_metrics_cache[(code, "close_metrics")] = snapshot
                self._close_metrics_last_cycle_at = datetime.now().isoformat(
                    timespec="seconds"
                )
                self._close_metrics_last_error = None
        except Exception as error:
            with self._lock:
                attempts = self._close_metrics_attempts.get(code, 0) + 1
                self._close_metrics_attempts[code] = attempts
                self._close_metrics_last_error = str(error)
                should_retry = attempts < self._close_metrics_max_attempts
                if should_retry:
                    self._close_metrics_queue.append(code)
                    self._close_metrics_queued.add(code)
            if self.store is not None:
                self.store.update_close_metrics(
                    code,
                    {
                        "orderbook_source": "query_snapshot",
                        "orderbook_status": "pending" if should_retry else "error",
                        "orderbook_status_detail": str(error),
                        "strength_source": "opt10046",
                        "strength_status": "pending" if should_retry else "error",
                        "strength_status_detail": str(error),
                    },
                )

    def _query_close_metrics_snapshot(self, code):
        # opt10046 is documented as the official strength source, but this
        # provider currently has no COM TR receive path. Keep the queue safe
        # and observable until the exact TR field map is wired.
        return {
            "orderbook_source": "query_snapshot",
            "orderbook_status": "error",
            "strength_source": "opt10046",
            "strength_status": "error",
            "strength_status_detail": (
                "bulk close metrics TR query not wired: opt10046 probe only, "
                "orderbook probe uses opt10004 queue"
            ),
        }

    def _strength_probe_ready_state_locked(self):
        if not self._running:
            return False, "provider_not_running"
        if self._login_state != "connected":
            return False, f"login_state={self._login_state}"
        if self._tr_event_connected is not True:
            return False, "tr_event_not_connected"
        if self._control is None:
            return False, "control_unavailable"
        if self._qt_pump_running is not True:
            return False, "qt_pump_not_running"
        return True, "ready"

    def is_strength_probe_ready(self):
        with self._lock:
            return self._strength_probe_ready_state_locked()[0]

    def _strength_probe_deferred_result(
        self,
        code,
        requested_at=None,
        reason=None,
        trading_date=None,
    ):
        snapshot_at = datetime.now().isoformat(timespec="seconds")
        return {
            "stock_code": code,
            "trading_date": _normalize_trading_date(trading_date)
            or _normalize_trading_date(snapshot_at),
            "strength_source": "opt10046_probe",
            "strength_status": "deferred",
            "strength_error": reason or "provider_not_ready",
            "strength_requested_at": requested_at,
            "strength_snapshot_at": snapshot_at,
        }

    def enqueue_strength_probe(
        self,
        code,
        priority="active",
        force=False,
        trading_date=None,
    ):
        normalized_code = _stock_code(code)
        if normalized_code is None:
            raise ValueError(f"invalid stock code: {code!r}")
        requested_at = datetime.now().isoformat(timespec="seconds")
        trading_date = _normalize_trading_date(trading_date)
        with self._lock:
            cached = self._strength_probe_cache.get(normalized_code)
            snapshot_date = (
                _normalize_trading_date(cached.get("trading_date"))
                or _normalize_trading_date(cached.get("strength_snapshot_at"))
                if cached
                else ""
            )
            if (
                not force
                and cached
                and cached.get("strength_status") == "ok"
                and snapshot_date
                == (trading_date or _normalize_trading_date(requested_at))
            ):
                response = {
                    "ok": True,
                    "accepted": [normalized_code],
                    "status": "cached",
                    "message": "strength probe cache reused",
                    "requested_at": requested_at,
                    "snapshot_at": cached.get("strength_snapshot_at"),
                }
                self._strength_probe_last_result = dict(response)
                return response
            now = time.monotonic()
            ready, not_ready_reason = self._strength_probe_ready_state_locked()
            last_for_code = self._strength_probe_last_by_code.get(normalized_code)
            if not force and last_for_code and now - last_for_code < (
                self._strength_probe_duplicate_window_sec
            ):
                return self._strength_probe_error(
                    normalized_code,
                    "duplicate_probe_suppressed",
                )
            if (
                normalized_code in self._strength_probe_pending_codes
                or (
                    self._strength_probe_inflight
                    and self._strength_probe_inflight.get("stock_code")
                    == normalized_code
                )
            ):
                response = {
                    "ok": True,
                    "accepted": [normalized_code],
                    "status": "pending",
                    "message": "strength probe already pending",
                }
                self._strength_probe_last_result = dict(response)
                return response
            item = {
                "stock_code": normalized_code,
                "requested_at": requested_at,
                "trading_date": trading_date,
                "priority": priority,
                "next_retry_at_monotonic": (
                    now + self._strength_probe_not_ready_retry_sec
                    if not ready
                    else now
                ),
            }
            if priority == "active":
                self._strength_probe_pending.appendleft(item)
            else:
                self._strength_probe_pending.append(item)
            self._strength_probe_pending_codes.add(normalized_code)
            status = "pending" if ready else "deferred"
            error = None if ready else not_ready_reason
            self._strength_probe_not_ready_reason = error or "ready"
        response = {
            "ok": True,
            "accepted": [normalized_code],
            "status": status,
            "message": (
                "strength probe queued"
                if status == "pending"
                else "strength probe deferred until provider ready"
            ),
            "requested_at": requested_at,
        }
        if self.store is not None:
            self.store.update_close_metrics(
                normalized_code,
                {
                    "strength_source": "opt10046_probe",
                    "strength_status": status,
                    "strength_error": error,
                    "trading_date": trading_date
                    or _normalize_trading_date(requested_at),
                    "strength_requested_at": requested_at,
                    "strength_snapshot_at": requested_at,
                },
            )
        with self._lock:
            self._strength_probe_last_result = dict(response)
        return response

    def request_strength_tr_probe(self, code):
        return self.enqueue_strength_probe(code, priority="active", force=True)

    def _orderbook_probe_ready_state_locked(self):
        return self._strength_probe_ready_state_locked()

    def is_orderbook_probe_ready(self):
        with self._lock:
            return self._orderbook_probe_ready_state_locked()[0]

    def _orderbook_probe_deferred_result(self, code, requested_at=None, reason=None):
        snapshot_at = datetime.now().isoformat(timespec="seconds")
        return {
            "stock_code": code,
            "orderbook_source": "opt10004_probe",
            "orderbook_status": "deferred",
            "orderbook_error": reason or "provider_not_ready",
            "orderbook_requested_at": requested_at,
            "orderbook_snapshot_at": snapshot_at,
        }

    def enqueue_orderbook_probe(self, code, priority="active", force=False):
        normalized_code = _stock_code(code)
        if normalized_code is None:
            raise ValueError(f"invalid stock code: {code!r}")
        requested_at = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            cached = self._orderbook_probe_cache.get(normalized_code)
            snapshot_date = str(cached.get("orderbook_snapshot_at", ""))[:10] if cached else ""
            if (
                not force
                and cached
                and cached.get("orderbook_status") == "ok"
                and snapshot_date == requested_at[:10]
            ):
                response = {
                    "ok": True,
                    "accepted": [normalized_code],
                    "status": "cached",
                    "message": "orderbook probe cache reused",
                    "requested_at": requested_at,
                    "snapshot_at": cached.get("orderbook_snapshot_at"),
                }
                self._orderbook_probe_last_result = dict(response)
                return response
            now = time.monotonic()
            ready, not_ready_reason = self._orderbook_probe_ready_state_locked()
            last_for_code = self._orderbook_probe_last_by_code.get(normalized_code)
            if not force and last_for_code and now - last_for_code < (
                self._orderbook_probe_duplicate_window_sec
            ):
                return self._orderbook_probe_error(
                    normalized_code,
                    "duplicate_probe_suppressed",
                    requested_at=requested_at,
                )
            if (
                normalized_code in self._orderbook_probe_pending_codes
                or (
                    self._orderbook_probe_inflight
                    and self._orderbook_probe_inflight.get("stock_code")
                    == normalized_code
                )
            ):
                response = {
                    "ok": True,
                    "accepted": [normalized_code],
                    "status": "pending",
                    "message": "orderbook probe already pending",
                }
                self._orderbook_probe_last_result = dict(response)
                return response
            item = {
                "stock_code": normalized_code,
                "requested_at": requested_at,
                "priority": priority,
                "next_retry_at_monotonic": (
                    now + self._orderbook_probe_not_ready_retry_sec
                    if not ready
                    else now
                ),
            }
            if priority == "active":
                self._orderbook_probe_pending.appendleft(item)
            else:
                self._orderbook_probe_pending.append(item)
            self._orderbook_probe_pending_codes.add(normalized_code)
            status = "pending" if ready else "deferred"
            error = None if ready else not_ready_reason
            self._orderbook_probe_not_ready_reason = error or "ready"
        response = {
            "ok": True,
            "accepted": [normalized_code],
            "status": status,
            "message": (
                "orderbook probe queued"
                if status == "pending"
                else "orderbook probe deferred until provider ready"
            ),
            "requested_at": requested_at,
        }
        if self.store is not None:
            self.store.update_close_metrics(
                normalized_code,
                {
                    "orderbook_source": "opt10004_probe",
                    "orderbook_status": status,
                    "orderbook_error": error,
                    "orderbook_requested_at": requested_at,
                    "orderbook_snapshot_at": requested_at,
                },
            )
        with self._lock:
            self._orderbook_probe_last_result = dict(response)
        return response

    def _process_strength_probe_queue(self):
        with self._lock:
            self._expire_strength_probe_inflight_locked()
            if self._strength_probe_inflight is not None:
                return
            if not self._strength_probe_pending:
                return
            now = time.monotonic()
            if now - self._strength_probe_last_request_at < (
                self._strength_probe_min_interval_sec
            ):
                return
            ready, not_ready_reason = self._strength_probe_ready_state_locked()
            if not ready:
                item = self._strength_probe_pending[0]
                retry_at = item.get("next_retry_at_monotonic") or 0
                if now < retry_at:
                    return
                item["next_retry_at_monotonic"] = (
                    now + self._strength_probe_not_ready_retry_sec
                )
                code = item["stock_code"]
                requested_at = item.get("requested_at")
                result = self._strength_probe_deferred_result(
                    code,
                    requested_at=requested_at,
                    reason=not_ready_reason,
                    trading_date=item.get("trading_date"),
                )
                self._strength_probe_not_ready_reason = not_ready_reason
                self._strength_probe_last_error = not_ready_reason
                self._strength_probe_last_result = dict(result)
                if self.store is not None:
                    self.store.update_close_metrics(code, result)
                return
            item = self._strength_probe_pending.popleft()
            self._strength_probe_pending_codes.discard(item["stock_code"])
        self._request_strength_tr_probe_on_qt(item)

    def _expire_strength_probe_inflight_locked(self):
        inflight = self._strength_probe_inflight
        if not inflight:
            return
        started_at = inflight.get("started_at_monotonic")
        if started_at is None:
            return
        if time.monotonic() - started_at < self._strength_probe_timeout_sec:
            return
        code = inflight.get("stock_code")
        requested_at = inflight.get("requested_at")
        self._strength_probe_inflight = None
        self._strength_probe_error(
            code,
            "opt10046 probe timeout",
            requested_at=requested_at,
            trading_date=inflight.get("trading_date"),
        )

    def _request_strength_tr_probe_on_qt(self, item):
        normalized_code = item["stock_code"]
        requested_at = item.get("requested_at") or datetime.now().isoformat(
            timespec="seconds"
        )
        trading_date = _normalize_trading_date(item.get("trading_date"))
        with self._lock:
            ready, not_ready_reason = self._strength_probe_ready_state_locked()
            if not ready:
                item["next_retry_at_monotonic"] = (
                    time.monotonic() + self._strength_probe_not_ready_retry_sec
                )
                self._strength_probe_pending.appendleft(item)
                self._strength_probe_pending_codes.add(normalized_code)
                result = self._strength_probe_deferred_result(
                    normalized_code,
                    requested_at=requested_at,
                    reason=not_ready_reason,
                    trading_date=trading_date,
                )
                self._strength_probe_not_ready_reason = not_ready_reason
                self._strength_probe_last_error = not_ready_reason
                self._strength_probe_last_result = dict(result)
                if self.store is not None:
                    self.store.update_close_metrics(normalized_code, result)
                return
            control = self._control
            self._strength_probe_last_request_at = time.monotonic()
            self._strength_probe_last_by_code[normalized_code] = (
                self._strength_probe_last_request_at
            )
        if control is None:
            self._strength_probe_error(
                normalized_code,
                "control_unavailable",
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        # opt10046 code suffix is unconfirmed locally. Probe uses six digits first;
        # _AL retry is intentionally left for a later guarded probe.
        try:
            control.dynamicCall(
                "SetInputValue(QString, QString)",
                "\uc885\ubaa9\ucf54\ub4dc",
                normalized_code,
            )
            result = control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                self._STRENGTH_PROBE_RQNAME,
                self._STRENGTH_PROBE_TRCODE,
                0,
                self._STRENGTH_PROBE_SCREEN,
            )
        except Exception as error:
            self._strength_probe_error(
                normalized_code,
                str(error),
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        if result not in (None, 0, "0"):
            self._strength_probe_error(
                normalized_code,
                f"CommRqData returned {result!r}",
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        requested = datetime.now().isoformat(timespec="seconds")
        inflight = {
            "stock_code": normalized_code,
            "trading_date": trading_date or _normalize_trading_date(requested),
            "requested_at": requested_at,
            "comm_requested_at": requested,
            "rqname": self._STRENGTH_PROBE_RQNAME,
            "trcode": self._STRENGTH_PROBE_TRCODE,
            "screen_no": self._STRENGTH_PROBE_SCREEN,
            "started_at_monotonic": time.monotonic(),
        }
        response = {
            "stock_code": normalized_code,
            "trading_date": trading_date or _normalize_trading_date(requested),
            "strength_source": "opt10046_probe",
            "strength_status": "requested",
            "strength_requested_at": requested_at,
            "strength_snapshot_at": requested,
            "strength_rqname": self._STRENGTH_PROBE_RQNAME,
            "strength_trcode": self._STRENGTH_PROBE_TRCODE,
            "strength_screen_no": self._STRENGTH_PROBE_SCREEN,
        }
        if self.store is not None:
            self.store.update_close_metrics(normalized_code, response)
        with self._lock:
            self._strength_probe_inflight = inflight
            self._strength_probe_last_result = dict(response)

    def _strength_probe_error(
        self,
        code,
        message,
        requested_at=None,
        trading_date=None,
    ):
        completed_at = datetime.now().isoformat(timespec="seconds")
        result = {
            "stock_code": code,
            "trading_date": _normalize_trading_date(trading_date)
            or _normalize_trading_date(completed_at),
            "strength_source": "opt10046_probe",
            "strength_status": "error",
            "strength_error": str(message)[:160],
            "strength_requested_at": requested_at,
            "strength_completed_at": completed_at,
            "strength_snapshot_at": completed_at,
        }
        self._strength_probe_last_error = result["strength_error"]
        self._strength_probe_last_result = dict(result)
        self._strength_probe_cache[code] = dict(result)
        if self.store is not None:
            self.store.update_close_metrics(code, result)
        return result

    def _process_orderbook_probe_queue(self):
        with self._lock:
            self._expire_orderbook_probe_inflight_locked()
            if self._orderbook_probe_inflight is not None:
                return
            if not self._orderbook_probe_pending:
                return
            now = time.monotonic()
            if now - self._orderbook_probe_last_request_at < (
                self._orderbook_probe_min_interval_sec
            ):
                return
            ready, not_ready_reason = self._orderbook_probe_ready_state_locked()
            if not ready:
                item = self._orderbook_probe_pending[0]
                retry_at = item.get("next_retry_at_monotonic") or 0
                if now < retry_at:
                    return
                item["next_retry_at_monotonic"] = (
                    now + self._orderbook_probe_not_ready_retry_sec
                )
                code = item["stock_code"]
                requested_at = item.get("requested_at")
                result = self._orderbook_probe_deferred_result(
                    code,
                    requested_at=requested_at,
                    reason=not_ready_reason,
                )
                self._orderbook_probe_not_ready_reason = not_ready_reason
                self._orderbook_probe_last_error = not_ready_reason
                self._orderbook_probe_last_result = dict(result)
                if self.store is not None:
                    self.store.update_close_metrics(code, result)
                return
            item = self._orderbook_probe_pending.popleft()
            self._orderbook_probe_pending_codes.discard(item["stock_code"])
        self._request_orderbook_tr_probe_on_qt(item)

    def _expire_orderbook_probe_inflight_locked(self):
        inflight = self._orderbook_probe_inflight
        if not inflight:
            return
        started_at = inflight.get("started_at_monotonic")
        if started_at is None:
            return
        if time.monotonic() - started_at < self._orderbook_probe_timeout_sec:
            return
        code = inflight.get("stock_code")
        requested_at = inflight.get("requested_at")
        self._orderbook_probe_inflight = None
        self._orderbook_probe_error(
            code,
            "opt10004 probe timeout",
            requested_at=requested_at,
            status="timeout",
        )

    def _request_orderbook_tr_probe_on_qt(self, item):
        normalized_code = item["stock_code"]
        requested_at = item.get("requested_at") or datetime.now().isoformat(
            timespec="seconds"
        )
        with self._lock:
            ready, not_ready_reason = self._orderbook_probe_ready_state_locked()
            if not ready:
                item["next_retry_at_monotonic"] = (
                    time.monotonic() + self._orderbook_probe_not_ready_retry_sec
                )
                self._orderbook_probe_pending.appendleft(item)
                self._orderbook_probe_pending_codes.add(normalized_code)
                result = self._orderbook_probe_deferred_result(
                    normalized_code,
                    requested_at=requested_at,
                    reason=not_ready_reason,
                )
                self._orderbook_probe_not_ready_reason = not_ready_reason
                self._orderbook_probe_last_error = not_ready_reason
                self._orderbook_probe_last_result = dict(result)
                if self.store is not None:
                    self.store.update_close_metrics(normalized_code, result)
                return
            control = self._control
            self._orderbook_probe_last_request_at = time.monotonic()
            self._orderbook_probe_last_by_code[normalized_code] = (
                self._orderbook_probe_last_request_at
            )
        if control is None:
            self._orderbook_probe_error(
                normalized_code,
                "control_unavailable",
                requested_at=requested_at,
            )
            return
        try:
            control.dynamicCall(
                "SetInputValue(QString, QString)",
                "\uc885\ubaa9\ucf54\ub4dc",
                normalized_code,
            )
            result = control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                self._ORDERBOOK_PROBE_RQNAME,
                self._ORDERBOOK_PROBE_TRCODE,
                0,
                self._ORDERBOOK_PROBE_SCREEN,
            )
        except Exception as error:
            self._orderbook_probe_error(
                normalized_code,
                str(error),
                requested_at=requested_at,
            )
            return
        if result not in (None, 0, "0"):
            self._orderbook_probe_error(
                normalized_code,
                f"CommRqData returned {result!r}",
                requested_at=requested_at,
            )
            return
        requested = datetime.now().isoformat(timespec="seconds")
        inflight = {
            "stock_code": normalized_code,
            "requested_at": requested_at,
            "comm_requested_at": requested,
            "rqname": self._ORDERBOOK_PROBE_RQNAME,
            "trcode": self._ORDERBOOK_PROBE_TRCODE,
            "screen_no": self._ORDERBOOK_PROBE_SCREEN,
            "started_at_monotonic": time.monotonic(),
        }
        response = {
            "stock_code": normalized_code,
            "orderbook_source": "opt10004_probe",
            "orderbook_status": "requested",
            "orderbook_requested_at": requested_at,
            "orderbook_snapshot_at": requested,
            "orderbook_rqname": self._ORDERBOOK_PROBE_RQNAME,
            "orderbook_trcode": self._ORDERBOOK_PROBE_TRCODE,
            "orderbook_screen_no": self._ORDERBOOK_PROBE_SCREEN,
        }
        if self.store is not None:
            self.store.update_close_metrics(normalized_code, response)
        with self._lock:
            self._orderbook_probe_inflight = inflight
            self._orderbook_probe_last_result = dict(response)

    def _orderbook_probe_error(
        self, code, message, requested_at=None, status="error"
    ):
        completed_at = datetime.now().isoformat(timespec="seconds")
        result = {
            "stock_code": code,
            "orderbook_source": "opt10004_probe",
            "orderbook_status": status,
            "orderbook_error": str(message)[:160],
            "orderbook_requested_at": requested_at,
            "orderbook_completed_at": completed_at,
            "orderbook_snapshot_at": completed_at,
        }
        self._orderbook_probe_last_error = result["orderbook_error"]
        self._orderbook_probe_last_result = dict(result)
        if status in {"error", "timeout"}:
            self._orderbook_probe_cache[code] = dict(result)
        if self.store is not None:
            self.store.update_close_metrics(code, result)
        return result

    def _opt10055_probe_ready_state_locked(self):
        return self._strength_probe_ready_state_locked()

    def request_opt10055_probe(
        self,
        codes,
        day="1",
        limit=30,
        wait_timeout_sec=12.0,
        threshold_krw=LARGE_TRADE_THRESHOLD_KRW,
        apply=False,
        trading_date=None,
    ):
        normalized_codes = []
        for code in codes or []:
            normalized_code = _stock_code(code)
            if normalized_code is not None:
                normalized_codes.append(normalized_code)
        normalized_codes = list(dict.fromkeys(normalized_codes))[:5]
        day = str(day or "1").strip()
        if day not in {"1", "2"}:
            day = "1"
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 30
        limit = min(max(1, limit), 100)
        threshold_krw = self._normalize_large_trade_threshold_krw(threshold_krw)
        requested_at = datetime.now().isoformat(timespec="seconds")
        trading_date = _normalize_trading_date(trading_date)
        enqueue_results = []
        for code in normalized_codes:
            enqueue_results.append(
                self.enqueue_opt10055_probe(
                    code,
                    day=day,
                    limit=limit,
                    threshold_krw=threshold_krw,
                    apply=apply,
                    requested_at=requested_at,
                    trading_date=trading_date,
                )
            )
        deadline = time.monotonic() + max(0.0, float(wait_timeout_sec or 0))
        while normalized_codes and time.monotonic() < deadline:
            with self._lock:
                complete_count = sum(
                    1
                    for code in normalized_codes
                    if self._opt10055_probe_cache.get(
                        (code, day, limit, threshold_krw), {}
                    ).get("opt10055_completed_at")
                    and str(
                        self._opt10055_probe_cache[
                            (code, day, limit, threshold_krw)
                        ].get(
                            "opt10055_requested_at", ""
                        )
                    )
                    >= requested_at
                )
            if complete_count >= len(normalized_codes):
                break
            time.sleep(0.05)
        results = {}
        with self._lock:
            for code in normalized_codes:
                cached = self._opt10055_probe_cache.get(
                    (code, day, limit, threshold_krw)
                )
                if cached:
                    results[code] = dict(cached)
                else:
                    results[code] = self._opt10055_probe_deferred_result(
                        code,
                        day=day,
                        limit=limit,
                        threshold_krw=threshold_krw,
                        requested_at=requested_at,
                        reason="probe_result_wait_timeout",
                        trading_date=trading_date,
                    )
        return {
            "ok": True,
            "tr_code": self._OPT10055_PROBE_TRCODE,
            "day": day,
            "large_trade_threshold_krw": threshold_krw,
            "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
            "apply": bool(apply),
            "requested_codes": normalized_codes,
            "results": results,
            "notes": [
                "debug probe only; TR runs only when this endpoint is called",
                "codes capped at 5 and limit capped at 100",
                "side uses the raw trade quantity sign; unsigned or zero rows are unknown",
            ],
            "payload_mode": "debug_probe",
            "requested_at": requested_at,
            "trading_date": trading_date or _normalize_trading_date(requested_at),
            "enqueue_results": enqueue_results,
        }

    @staticmethod
    def _normalize_large_trade_threshold_krw(value):
        try:
            threshold = int(float(value))
        except (TypeError, ValueError):
            threshold = LARGE_TRADE_THRESHOLD_KRW
        return max(1, threshold)

    def enqueue_opt10055_probe(
        self,
        code,
        day="1",
        limit=30,
        threshold_krw=LARGE_TRADE_THRESHOLD_KRW,
        apply=False,
        requested_at=None,
        trading_date=None,
    ):
        normalized_code = _stock_code(code)
        if normalized_code is None:
            raise ValueError(f"invalid stock code: {code!r}")
        day = str(day or "1").strip()
        if day not in {"1", "2"}:
            raise ValueError(f"invalid OPT10055 day: {day!r}")
        limit = min(max(1, int(limit or 30)), 100)
        threshold_krw = self._normalize_large_trade_threshold_krw(threshold_krw)
        requested_at = requested_at or datetime.now().isoformat(timespec="seconds")
        trading_date = _normalize_trading_date(trading_date)
        key = (normalized_code, day, limit, threshold_krw)
        with self._lock:
            now = time.monotonic()
            ready, not_ready_reason = self._opt10055_probe_ready_state_locked()
            if (
                key in self._opt10055_probe_pending_keys
                or (
                    self._opt10055_probe_inflight
                    and self._opt10055_probe_inflight.get("key") == key
                )
            ):
                response = {
                    "ok": True,
                    "accepted": [normalized_code],
                    "status": "pending",
                    "message": "opt10055 probe already pending",
                }
                self._opt10055_probe_last_result = dict(response)
                return response
            item = {
                "stock_code": normalized_code,
                "day": day,
                "limit": limit,
                "threshold_krw": threshold_krw,
                "apply": bool(apply),
                "key": key,
                "requested_at": requested_at,
                "trading_date": trading_date,
                "next_retry_at_monotonic": (
                    now + self._opt10055_probe_not_ready_retry_sec
                    if not ready
                    else now
                ),
            }
            self._opt10055_probe_pending.append(item)
            self._opt10055_probe_pending_keys.add(key)
            self._opt10055_probe_cache.pop(key, None)
            status = "pending" if ready else "deferred"
            error = None if ready else not_ready_reason
            self._opt10055_probe_not_ready_reason = error or "ready"
        response = {
            "ok": True,
            "accepted": [normalized_code],
            "status": status,
            "message": (
                "opt10055 probe queued"
                if status == "pending"
                else "opt10055 probe deferred until provider ready"
            ),
            "requested_at": requested_at,
            "trading_date": trading_date or _normalize_trading_date(requested_at),
        }
        with self._lock:
            self._opt10055_probe_last_result = dict(response)
        return response

    def _opt10055_probe_deferred_result(
        self,
        code,
        day="1",
        limit=30,
        threshold_krw=LARGE_TRADE_THRESHOLD_KRW,
        requested_at=None,
        reason=None,
        trading_date=None,
    ):
        snapshot_at = datetime.now().isoformat(timespec="seconds")
        threshold_krw = self._normalize_large_trade_threshold_krw(threshold_krw)
        return {
            "stock_code": code,
            "trading_date": _normalize_trading_date(trading_date)
            or _normalize_trading_date(snapshot_at),
            "day": str(day),
            "limit": int(limit),
            "large_trade_threshold_krw": threshold_krw,
            "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
            "opt10055_source": "opt10055_probe",
            "opt10055_status": "deferred",
            "opt10055_error": reason or "provider_not_ready",
            "opt10055_requested_at": requested_at,
            "opt10055_snapshot_at": snapshot_at,
            "rows": [],
            "summary": self._opt10055_summary([], threshold_krw=threshold_krw),
        }

    def _process_opt10055_probe_queue(self):
        with self._lock:
            self._expire_opt10055_probe_inflight_locked()
            if self._opt10055_probe_inflight is not None:
                return
            if not self._opt10055_probe_pending:
                return
            now = time.monotonic()
            if now - self._opt10055_probe_last_request_at < (
                self._opt10055_probe_min_interval_sec
            ):
                return
            ready, not_ready_reason = self._opt10055_probe_ready_state_locked()
            if not ready:
                item = self._opt10055_probe_pending[0]
                retry_at = item.get("next_retry_at_monotonic") or 0
                if now < retry_at:
                    return
                item["next_retry_at_monotonic"] = (
                    now + self._opt10055_probe_not_ready_retry_sec
                )
                result = self._opt10055_probe_deferred_result(
                    item.get("stock_code"),
                    day=item.get("day"),
                    limit=item.get("limit"),
                    threshold_krw=item.get("threshold_krw"),
                    requested_at=item.get("requested_at"),
                    reason=not_ready_reason,
                    trading_date=item.get("trading_date"),
                )
                self._opt10055_probe_not_ready_reason = not_ready_reason
                self._opt10055_probe_last_error = not_ready_reason
                self._opt10055_probe_last_result = dict(result)
                self._opt10055_probe_cache[item["key"]] = dict(result)
                return
            item = self._opt10055_probe_pending.popleft()
            self._opt10055_probe_pending_keys.discard(item["key"])
        self._request_opt10055_probe_on_qt(item)

    def _expire_opt10055_probe_inflight_locked(self):
        inflight = self._opt10055_probe_inflight
        if not inflight:
            return
        started_at = inflight.get("started_at_monotonic")
        if started_at is None:
            return
        if time.monotonic() - started_at < self._opt10055_probe_timeout_sec:
            return
        self._opt10055_probe_inflight = None
        self._opt10055_probe_error(
            inflight.get("stock_code"),
            "opt10055 probe timeout",
            day=inflight.get("day"),
            limit=inflight.get("limit"),
            threshold_krw=inflight.get("threshold_krw"),
            requested_at=inflight.get("requested_at"),
            status="timeout",
            trading_date=inflight.get("trading_date"),
        )

    def _request_opt10055_probe_on_qt(self, item):
        normalized_code = item["stock_code"]
        requested_at = item.get("requested_at") or datetime.now().isoformat(
            timespec="seconds"
        )
        trading_date = _normalize_trading_date(item.get("trading_date"))
        day = item.get("day") or "1"
        limit = item.get("limit") or 30
        threshold_krw = self._normalize_large_trade_threshold_krw(
            item.get("threshold_krw")
        )
        with self._lock:
            ready, not_ready_reason = self._opt10055_probe_ready_state_locked()
            if not ready:
                item["next_retry_at_monotonic"] = (
                    time.monotonic() + self._opt10055_probe_not_ready_retry_sec
                )
                self._opt10055_probe_pending.appendleft(item)
                self._opt10055_probe_pending_keys.add(item["key"])
                result = self._opt10055_probe_deferred_result(
                    normalized_code,
                    day=day,
                    limit=limit,
                    threshold_krw=threshold_krw,
                    requested_at=requested_at,
                    reason=not_ready_reason,
                    trading_date=trading_date,
                )
                self._opt10055_probe_not_ready_reason = not_ready_reason
                self._opt10055_probe_last_error = not_ready_reason
                self._opt10055_probe_last_result = dict(result)
                self._opt10055_probe_cache[item["key"]] = dict(result)
                return
            control = self._control
            self._opt10055_probe_last_request_at = time.monotonic()
        if control is None:
            self._opt10055_probe_error(
                normalized_code,
                "control_unavailable",
                day=day,
                limit=limit,
                threshold_krw=threshold_krw,
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        try:
            control.dynamicCall(
                "SetInputValue(QString, QString)",
                "\uc885\ubaa9\ucf54\ub4dc",
                normalized_code,
            )
            control.dynamicCall(
                "SetInputValue(QString, QString)",
                "\ub2f9\uc77c\uc804\uc77c",
                str(day),
            )
            result = control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                self._OPT10055_PROBE_RQNAME,
                self._OPT10055_PROBE_TRCODE,
                0,
                self._OPT10055_PROBE_SCREEN,
            )
        except Exception as error:
            self._opt10055_probe_error(
                normalized_code,
                str(error),
                day=day,
                limit=limit,
                threshold_krw=threshold_krw,
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        if result not in (None, 0, "0"):
            self._opt10055_probe_error(
                normalized_code,
                f"CommRqData returned {result!r}",
                day=day,
                limit=limit,
                threshold_krw=threshold_krw,
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        requested = datetime.now().isoformat(timespec="seconds")
        inflight = {
            "stock_code": normalized_code,
            "day": str(day),
            "limit": int(limit),
            "threshold_krw": threshold_krw,
            "apply": bool(item.get("apply")),
            "key": item["key"],
            "requested_at": requested_at,
            "trading_date": trading_date,
            "comm_requested_at": requested,
            "rqname": self._OPT10055_PROBE_RQNAME,
            "trcode": self._OPT10055_PROBE_TRCODE,
            "screen_no": self._OPT10055_PROBE_SCREEN,
            "started_at_monotonic": time.monotonic(),
        }
        response = {
            "stock_code": normalized_code,
            "trading_date": trading_date or _normalize_trading_date(requested),
            "day": str(day),
            "limit": int(limit),
            "large_trade_threshold_krw": threshold_krw,
            "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
            "apply": bool(item.get("apply")),
            "opt10055_source": "opt10055_probe",
            "opt10055_status": "requested",
            "opt10055_requested_at": requested_at,
            "opt10055_snapshot_at": requested,
            "opt10055_rqname": self._OPT10055_PROBE_RQNAME,
            "opt10055_trcode": self._OPT10055_PROBE_TRCODE,
            "opt10055_screen_no": self._OPT10055_PROBE_SCREEN,
        }
        with self._lock:
            self._opt10055_probe_inflight = inflight
            self._opt10055_probe_last_result = dict(response)

    def _opt10055_probe_error(
        self,
        code,
        message,
        day="1",
        limit=30,
        threshold_krw=LARGE_TRADE_THRESHOLD_KRW,
        requested_at=None,
        status="error",
        trading_date=None,
    ):
        completed_at = datetime.now().isoformat(timespec="seconds")
        threshold_krw = self._normalize_large_trade_threshold_krw(threshold_krw)
        result = {
            "stock_code": code,
            "trading_date": _normalize_trading_date(trading_date)
            or _normalize_trading_date(completed_at),
            "day": str(day),
            "limit": int(limit or 30),
            "large_trade_threshold_krw": threshold_krw,
            "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
            "opt10055_source": "opt10055_probe",
            "opt10055_status": status,
            "opt10055_error": str(message)[:160],
            "opt10055_requested_at": requested_at,
            "opt10055_completed_at": completed_at,
            "opt10055_snapshot_at": completed_at,
            "rows": [],
            "summary": self._opt10055_summary([], threshold_krw=threshold_krw),
        }
        self._opt10055_probe_last_error = result["opt10055_error"]
        self._opt10055_probe_last_result = dict(result)
        self._opt10055_probe_cache[
            (code, str(day), int(limit or 30), threshold_krw)
        ] = dict(result)
        return result

    def _on_receive_tr_data(self, *args):
        try:
            self._handle_receive_tr_data(*args)
        except Exception as error:
            with self._lock:
                self._strength_probe_last_error = f"OnReceiveTrData failed: {error}"

    def _handle_receive_tr_data(self, *args):
        if len(args) < 3:
            return
        screen_no = str(args[0])
        rq_name = str(args[1])
        tr_code = str(args[2])
        record_name = str(args[3]) if len(args) > 3 else ""
        if rq_name == self._ORDERBOOK_PROBE_RQNAME:
            self._handle_orderbook_tr_data(
                screen_no,
                rq_name,
                tr_code,
                record_name,
            )
            return
        if rq_name == self._OPT10055_PROBE_RQNAME:
            self._handle_opt10055_tr_data(
                screen_no,
                rq_name,
                tr_code,
                record_name,
            )
            return
        with self._lock:
            inflight = self._strength_probe_inflight
            if inflight is None or rq_name != inflight.get("rqname"):
                return
            self._strength_probe_inflight = None
            control = self._control
        if rq_name != self._STRENGTH_PROBE_RQNAME:
            return
        code = inflight.get("stock_code")
        requested_at = inflight.get("requested_at")
        trading_date = _normalize_trading_date(inflight.get("trading_date"))
        if control is None or code is None:
            self._strength_probe_error(
                code or "",
                "control_unavailable_on_receive",
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        repeat_count = 0
        try:
            repeat_count = control.dynamicCall(
                "GetRepeatCnt(QString, QString)",
                tr_code,
                rq_name,
            )
        except Exception:
            repeat_count = 0
        try:
            repeat_count = int(repeat_count or 0)
        except (TypeError, ValueError):
            repeat_count = 0
        row_index = 0
        raw_sample = []
        values = {}
        for field_name in self._STRENGTH_PROBE_FIELDS:
            raw_value = control.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                tr_code,
                rq_name,
                row_index,
                field_name,
            )
            text = "" if raw_value is None else str(raw_value).strip()
            raw_sample.append({"field": field_name, "value": text})
            values[field_name] = self._realdata_number(text)
        snapshot_at = datetime.now().isoformat(timespec="seconds")
        has_any_value = any(value is not None for value in values.values())
        result = {
            "stock_code": code,
            "trading_date": trading_date or _normalize_trading_date(snapshot_at),
            "realtime_strength_snapshot": values.get(self._STRENGTH_PROBE_FIELDS[0]),
            "strength_5m": values.get(self._STRENGTH_PROBE_FIELDS[1]),
            "strength_20m": values.get(self._STRENGTH_PROBE_FIELDS[2]),
            "strength_60m": values.get(self._STRENGTH_PROBE_FIELDS[3]),
            "strength_source": "opt10046",
            "strength_snapshot_at": snapshot_at,
            "strength_completed_at": snapshot_at,
            "strength_requested_at": requested_at,
            "strength_stale_sec": 0,
            "strength_status": "ok" if has_any_value else "no_data",
            "strength_error": None if has_any_value else "empty opt10046 fields",
            "strength_tr_repeat_count": repeat_count,
            "strength_raw_sample": raw_sample[:5],
            "strength_rqname": rq_name,
            "strength_trcode": tr_code,
            "strength_screen_no": screen_no,
            "strength_status_detail": (
                f"screen={screen_no}, tr={tr_code}, record={record_name}, "
                f"repeat_count={repeat_count}"
            ),
        }
        if self.store is not None:
            self.store.update_close_metrics(code, result)
        with self._lock:
            self._strength_probe_cache[code] = dict(result)
            self._strength_probe_last_raw_sample = raw_sample[:5]
            self._strength_probe_last_result = dict(result)
            self._strength_probe_last_error = result.get("strength_error")

    def _handle_orderbook_tr_data(self, screen_no, rq_name, tr_code, record_name):
        with self._lock:
            inflight = self._orderbook_probe_inflight
            if inflight is None or rq_name != inflight.get("rqname"):
                return
            self._orderbook_probe_inflight = None
            control = self._control
        code = inflight.get("stock_code")
        requested_at = inflight.get("requested_at")
        if control is None or code is None:
            self._orderbook_probe_error(
                code or "",
                "control_unavailable_on_receive",
                requested_at=requested_at,
            )
            return
        repeat_count = 0
        try:
            repeat_count = control.dynamicCall(
                "GetRepeatCnt(QString, QString)",
                tr_code,
                rq_name,
            )
        except Exception:
            repeat_count = 0
        try:
            repeat_count = int(repeat_count or 0)
        except (TypeError, ValueError):
            repeat_count = 0
        row_index = 0
        raw_values = {}
        raw_sample = []
        field_names = (
            self._ORDERBOOK_PROBE_TOTAL_ASK_FIELDS
            + self._ORDERBOOK_PROBE_TOTAL_BID_FIELDS
            + self._ORDERBOOK_PROBE_LEVEL_ASK_FIELDS
            + self._ORDERBOOK_PROBE_LEVEL_BID_FIELDS
            + self._ORDERBOOK_PROBE_PRICE_FIELDS
        )
        for field_name in field_names:
            raw_value = control.dynamicCall(
                "GetCommData(QString, QString, int, QString)",
                tr_code,
                rq_name,
                row_index,
                field_name,
            )
            text = "" if raw_value is None else str(raw_value).strip()
            number = self._realdata_number(text)
            raw_values[field_name] = number
            if text and len(raw_sample) < 10:
                raw_sample.append({"field": field_name, "value": text})
        normalized = self._normalize_orderbook_probe_values(raw_values)
        snapshot_at = datetime.now().isoformat(timespec="seconds")
        result = {
            "stock_code": code,
            "bid_volume_snapshot": normalized.get("bid_volume_snapshot"),
            "ask_volume_snapshot": normalized.get("ask_volume_snapshot"),
            "bid_pct": normalized.get("bid_pct"),
            "ask_pct": normalized.get("ask_pct"),
            "bid_ask_ratio_snapshot": normalized.get("bid_ask_ratio_snapshot"),
            "orderbook_source": normalized.get("orderbook_source"),
            "orderbook_snapshot_at": snapshot_at,
            "orderbook_completed_at": snapshot_at,
            "orderbook_requested_at": requested_at,
            "orderbook_stale_sec": 0,
            "orderbook_status": normalized.get("orderbook_status"),
            "orderbook_error": normalized.get("orderbook_error"),
            "orderbook_tr_repeat_count": repeat_count,
            "orderbook_raw_sample": raw_sample[:10],
            "orderbook_rqname": rq_name,
            "orderbook_trcode": tr_code,
            "orderbook_screen_no": screen_no,
            "orderbook_status_detail": (
                f"screen={screen_no}, tr={tr_code}, record={record_name}, "
                f"repeat_count={repeat_count}"
            ),
        }
        if self.store is not None:
            self.store.update_close_metrics(code, result)
        with self._lock:
            self._orderbook_probe_cache[code] = dict(result)
            self._orderbook_probe_last_raw_sample = raw_sample[:10]
            self._orderbook_probe_last_result = dict(result)
            self._orderbook_probe_last_error = result.get("orderbook_error")

    def _handle_opt10055_tr_data(self, screen_no, rq_name, tr_code, record_name):
        with self._lock:
            inflight = self._opt10055_probe_inflight
            if inflight is None or rq_name != inflight.get("rqname"):
                return
            self._opt10055_probe_inflight = None
            control = self._control
        code = inflight.get("stock_code")
        day = inflight.get("day") or "1"
        limit = int(inflight.get("limit") or 30)
        threshold_krw = self._normalize_large_trade_threshold_krw(
            inflight.get("threshold_krw")
        )
        apply_result = bool(inflight.get("apply"))
        requested_at = inflight.get("requested_at")
        trading_date = _normalize_trading_date(inflight.get("trading_date"))
        if control is None or code is None:
            self._opt10055_probe_error(
                code or "",
                "control_unavailable_on_receive",
                day=day,
                limit=limit,
                threshold_krw=threshold_krw,
                requested_at=requested_at,
                trading_date=trading_date,
            )
            return
        repeat_count = 0
        try:
            repeat_count = control.dynamicCall(
                "GetRepeatCnt(QString, QString)",
                tr_code,
                rq_name,
            )
        except Exception:
            repeat_count = 0
        try:
            repeat_count = int(repeat_count or 0)
        except (TypeError, ValueError):
            repeat_count = 0
        rows = []
        raw_sample = []
        for row_index in range(min(max(0, repeat_count), limit)):
            raw_values = {}
            for field_name in self._OPT10055_PROBE_FIELDS:
                raw_value = control.dynamicCall(
                    "GetCommData(QString, QString, int, QString)",
                    tr_code,
                    rq_name,
                    row_index,
                    field_name,
                )
                text = "" if raw_value is None else str(raw_value).strip()
                raw_values[field_name] = text
            parsed = self._parse_opt10055_row(
                raw_values,
                threshold_krw=threshold_krw,
            )
            rows.append(parsed)
            if len(raw_sample) < 5:
                raw_sample.append(dict(raw_values))
        snapshot_at = datetime.now().isoformat(timespec="seconds")
        summary = self._opt10055_summary(rows, threshold_krw=threshold_krw)
        applied_metrics = None
        apply_error = None
        if apply_result:
            applied_metrics = self._opt10055_summary_to_store_metrics(
                summary,
                snapshot_at,
                trading_date=trading_date,
            )
            if self.store is not None:
                try:
                    self.store.update_close_metrics(code, applied_metrics)
                except Exception as error:
                    apply_error = str(error)
            else:
                apply_error = "store_unavailable"
        result = {
            "stock_code": code,
            "trading_date": trading_date or _normalize_trading_date(snapshot_at),
            "day": str(day),
            "limit": limit,
            "large_trade_threshold_krw": threshold_krw,
            "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
            "apply": apply_result,
            "apply_error": apply_error,
            "applied_metrics": applied_metrics,
            "opt10055_source": "opt10055_probe",
            "opt10055_status": "ok" if repeat_count > 0 else "no_data",
            "opt10055_error": None if repeat_count > 0 else "empty opt10055 rows",
            "opt10055_snapshot_at": snapshot_at,
            "opt10055_completed_at": snapshot_at,
            "opt10055_requested_at": requested_at,
            "opt10055_tr_repeat_count": repeat_count,
            "opt10055_returned_sample_count": len(rows),
            "opt10055_raw_sample": raw_sample,
            "opt10055_rqname": rq_name,
            "opt10055_trcode": tr_code,
            "opt10055_screen_no": screen_no,
            "opt10055_status_detail": (
                f"screen={screen_no}, tr={tr_code}, record={record_name}, "
                f"repeat_count={repeat_count}"
            ),
            "rows": rows,
            "summary": summary,
        }
        with self._lock:
            self._opt10055_probe_cache[(code, str(day), limit, threshold_krw)] = dict(result)
            self._opt10055_probe_last_raw_sample = raw_sample
            self._opt10055_probe_last_result = dict(result)
            self._opt10055_probe_last_error = result.get("opt10055_error")

    def _parse_opt10055_row(
        self,
        raw_values,
        threshold_krw=LARGE_TRADE_THRESHOLD_KRW,
    ):
        threshold_krw = self._normalize_large_trade_threshold_krw(threshold_krw)
        price_raw = raw_values.get("\uccb4\uacb0\uac00")
        trade_qty_raw = raw_values.get("\uccb4\uacb0\ub7c9")
        price = normalize_kiwoom_price(price_raw)
        trade_qty = self._realdata_number(trade_qty_raw)
        trade_qty_abs = abs(trade_qty) if trade_qty is not None else None
        trade_amount_krw = (
            int(price * trade_qty_abs)
            if price is not None and trade_qty_abs is not None
            else None
        )
        trade_amount_eok = (
            round(trade_amount_krw / KRW_PER_EOK, 6)
            if trade_amount_krw is not None
            else None
        )
        raw_text = "" if trade_qty_raw is None else str(trade_qty_raw).strip()
        has_sign = raw_text.startswith(("+", "-"))
        if has_sign and trade_qty and trade_qty > 0:
            side = "buy"
        elif has_sign and trade_qty and trade_qty < 0:
            side = "sell"
        else:
            side = "unknown"
        row = {
            field_name: raw_values.get(field_name, "")
            for field_name in self._OPT10055_PROBE_FIELDS
        }
        row.update(
            {
                "price": price,
                "trade_qty_raw": trade_qty_raw,
                "trade_qty": trade_qty,
                "trade_qty_abs": trade_qty_abs,
                "trade_amount_krw": trade_amount_krw,
                "trade_amount_eok": trade_amount_eok,
                "large_trade_threshold_krw": threshold_krw,
                "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
                "is_large_trade": (
                    trade_amount_krw is not None
                    and trade_amount_krw >= threshold_krw
                ),
                "side": side,
                "is_big_1eok": (
                    trade_amount_krw is not None
                    and trade_amount_krw >= KRW_PER_EOK
                ),
            }
        )
        return row

    def _opt10055_summary(self, rows, threshold_krw=LARGE_TRADE_THRESHOLD_KRW):
        rows = rows or []
        threshold_krw = self._normalize_large_trade_threshold_krw(threshold_krw)
        signed_rows = [
            row
            for row in rows
            if str(row.get("trade_qty_raw") or "").strip().startswith(("+", "-"))
            and row.get("trade_qty") not in (None, 0)
        ]
        unsigned_rows = [row for row in rows if row not in signed_rows]
        large_rows = [
            row for row in rows if bool(row.get("is_large_trade"))
        ]
        signed_large_rows = [
            row for row in large_rows if row in signed_rows
        ]
        unsigned_large_rows = [
            row for row in large_rows if row not in signed_rows
        ]
        buy_large_rows = [
            row for row in signed_large_rows if row.get("side") == "buy"
        ]
        sell_large_rows = [
            row for row in signed_large_rows if row.get("side") == "sell"
        ]
        unknown_large_rows = [
            row for row in large_rows if row.get("side") == "unknown"
        ]
        under_threshold_rows = [
            row
            for row in rows
            if row.get("trade_amount_krw") is not None
            and row.get("trade_amount_krw") < threshold_krw
        ]
        buy_amount = sum(
            row.get("trade_amount_eok") or 0 for row in buy_large_rows
        )
        sell_amount = sum(
            row.get("trade_amount_eok") or 0 for row in sell_large_rows
        )
        unknown_amount = sum(
            row.get("trade_amount_eok") or 0 for row in unknown_large_rows
        )
        signed_amounts = [
            row.get("trade_amount_eok")
            for row in signed_rows
            if row.get("trade_amount_eok") is not None
        ]
        unknown_amounts = [
            row.get("trade_amount_eok")
            for row in unsigned_rows
            if row.get("trade_amount_eok") is not None
        ]
        if not rows:
            hint = "no_rows"
        elif signed_large_rows:
            hint = "signed_rows_usable_for_net_count"
        elif unsigned_large_rows:
            hint = "unsigned_large_rows_exist"
        elif len(under_threshold_rows) >= max(1, len(rows) // 2):
            hint = "kiwoom_rows_are_not_50m_based"
        else:
            hint = "kiwoom_rows_are_not_50m_based"
        return {
            "row_count": len(rows),
            "returned_sample_count": len(rows),
            "signed_qty_count": len(signed_rows),
            "unsigned_qty_count": len(rows) - len(signed_rows),
            "large_trade_count": len(large_rows),
            "signed_large_trade_count": len(signed_large_rows),
            "unsigned_large_trade_count": len(unsigned_large_rows),
            "large_trade_buy_count": len(buy_large_rows),
            "large_trade_sell_count": len(sell_large_rows),
            "large_trade_net_count": (
                len(buy_large_rows) - len(sell_large_rows)
            ),
            "large_trade_buy_sum_eok": round(buy_amount, 6),
            "large_trade_sell_sum_eok": round(sell_amount, 6),
            "large_trade_net_sum_eok": round(buy_amount - sell_amount, 6),
            "unknown_large_trade_count": len(unknown_large_rows),
            "unknown_large_trade_sum_eok": round(unknown_amount, 6),
            "max_signed_trade_amount_eok": (
                max(signed_amounts) if signed_amounts else None
            ),
            "max_unknown_trade_amount_eok": (
                max(unknown_amounts) if unknown_amounts else None
            ),
            "large_trade_threshold_krw": threshold_krw,
            "large_trade_threshold_eok": threshold_krw / KRW_PER_EOK,
            "buy_count_1eok": len(
                [
                    row for row in buy_large_rows
                    if row.get("is_big_1eok")
                ]
            ),
            "sell_count_1eok": len(
                [
                    row for row in sell_large_rows
                    if row.get("is_big_1eok")
                ]
            ),
            "kiwoom_large_trade_basis_hint": hint,
        }

    def _opt10055_summary_to_store_metrics(
        self,
        summary,
        snapshot_at,
        trading_date=None,
    ):
        buy_count = summary.get("large_trade_buy_count") or 0
        sell_count = summary.get("large_trade_sell_count") or 0
        net_count = summary.get("large_trade_net_count") or 0
        buy_sum = summary.get("large_trade_buy_sum_eok") or 0
        sell_sum = summary.get("large_trade_sell_sum_eok") or 0
        net_sum = summary.get("large_trade_net_sum_eok") or 0
        return {
            "trading_date": _normalize_trading_date(trading_date)
            or _normalize_trading_date(snapshot_at),
            "large_trade_source": "opt10055_day",
            "large_trade_threshold_krw": summary.get(
                "large_trade_threshold_krw"
            ),
            "large_trade_threshold_eok": summary.get(
                "large_trade_threshold_eok"
            ),
            "large_trade_buy_count": buy_count,
            "large_trade_sell_count": sell_count,
            "large_trade_net_count": net_count,
            "large_trade_buy_sum_eok": buy_sum,
            "large_trade_sell_sum_eok": sell_sum,
            "large_trade_net_sum_eok": net_sum,
            "large_trade_unknown_count": summary.get(
                "unknown_large_trade_count"
            ) or 0,
            "large_trade_unknown_sum_eok": summary.get(
                "unknown_large_trade_sum_eok"
            ) or 0,
            "large_trade_updated_at": snapshot_at,
            "large_trade_status": "ok",
            "big_hand_buy_count_1eok": buy_count,
            "big_hand_sell_count_1eok": sell_count,
            "big_hand_net_buy_count_1eok": net_count,
            "big_hand_buy_sum_eok": buy_sum,
            "big_hand_sell_sum_eok": sell_sum,
            "big_hand_net_sum_eok": net_sum,
        }

    def _normalize_orderbook_probe_values(self, values):
        ask_volume = self._first_probe_number(
            values, self._ORDERBOOK_PROBE_TOTAL_ASK_FIELDS
        )
        bid_volume = self._first_probe_number(
            values, self._ORDERBOOK_PROBE_TOTAL_BID_FIELDS
        )
        source = self._ORDERBOOK_PROBE_TRCODE
        if ask_volume is None:
            ask_volume = self._sum_probe_numbers(
                values, self._ORDERBOOK_PROBE_LEVEL_ASK_FIELDS
            )
            if ask_volume is not None:
                source = f"{self._ORDERBOOK_PROBE_TRCODE}_sum_10_levels"
        if bid_volume is None:
            bid_volume = self._sum_probe_numbers(
                values, self._ORDERBOOK_PROBE_LEVEL_BID_FIELDS
            )
            if bid_volume is not None:
                source = f"{self._ORDERBOOK_PROBE_TRCODE}_sum_10_levels"
        if ask_volume is None or bid_volume is None:
            return {
                "orderbook_source": "opt10004_probe",
                "orderbook_status": "no_data",
                "orderbook_error": "no total or 1-10 level volume fields",
            }
        total = bid_volume + ask_volume
        if total <= 0:
            return {
                "orderbook_source": source,
                "orderbook_status": "no_data",
                "orderbook_error": "zero total orderbook volume",
            }
        bid_pct = round((bid_volume / total) * 100)
        ask_pct = 100 - bid_pct
        bid_ask_ratio = round(bid_volume / ask_volume, 4) if ask_volume else None
        return {
            "bid_volume_snapshot": bid_volume,
            "ask_volume_snapshot": ask_volume,
            "bid_pct": bid_pct,
            "ask_pct": ask_pct,
            "bid_ask_ratio_snapshot": bid_ask_ratio,
            "orderbook_source": source,
            "orderbook_status": "ok",
            "orderbook_error": None,
        }

    @staticmethod
    def _first_probe_number(values, field_names):
        for field_name in field_names:
            value = values.get(field_name)
            if value is not None:
                return value
        return None

    @staticmethod
    def _sum_probe_numbers(values, field_names):
        numbers = [values.get(field_name) for field_name in field_names]
        numbers = [value for value in numbers if value is not None]
        if not numbers:
            return None
        return sum(numbers)

    def _limited_realtime_codes(self, codes):
        limit = self._realtime_code_limit
        if limit <= 0:
            return list(codes)
        return list(codes)[:limit]

    @staticmethod
    def _trade_lag_seconds(trade_time_raw, now=None):
        if trade_time_raw in (None, ""):
            return None
        digits = "".join(char for char in str(trade_time_raw).strip() if char.isdigit())
        if len(digits) < 6:
            return None
        digits = digits[:6]
        try:
            hour = int(digits[0:2])
            minute = int(digits[2:4])
            second = int(digits[4:6])
            current = now or datetime.now()
            trade_time = current.replace(
                hour=hour,
                minute=minute,
                second=second,
                microsecond=0,
            )
        except ValueError:
            return None
        return round((current - trade_time).total_seconds(), 3)

    @staticmethod
    def _trade_time_seconds(trade_time_raw):
        if trade_time_raw in (None, ""):
            return None
        digits = "".join(char for char in str(trade_time_raw).strip() if char.isdigit())
        if len(digits) < 6:
            return None
        digits = digits[:6]
        try:
            hour = int(digits[0:2])
            minute = int(digits[2:4])
            second = int(digits[4:6])
        except ValueError:
            return None
        if hour > 23 or minute > 59 or second > 59:
            return None
        return hour * 3600 + minute * 60 + second

    def _record_trade_lag(self, normalized_code, lag_sec):
        if lag_sec is None:
            return
        with self._lock:
            self._last_trade_lag_sec = lag_sec
            if self._max_trade_lag_sec is None or lag_sec > self._max_trade_lag_sec:
                self._max_trade_lag_sec = lag_sec
            self._recent_trade_lags.append(lag_sec)
            if normalized_code is not None:
                self._latest_trade_lag_by_code[normalized_code] = lag_sec
                if len(self._latest_trade_lag_by_code) > 40:
                    for stale_code in list(self._latest_trade_lag_by_code)[:10]:
                        self._latest_trade_lag_by_code.pop(stale_code, None)

    def _stale_trade_drop_sample(self, stock_code, real_type, received_code):
        if self._stale_trade_drop_seconds <= 0:
            return None
        with self._lock:
            control = self._control
            registered_codes = set(self._registered_codes)
            original_registered_codes = dict(self._original_registered_codes)
            registered_code_to_normalized = dict(
                self._registered_code_to_normalized
            )
        if control is None:
            return None
        trade_time_raw = control.dynamicCall(
            "GetCommRealData(QString, int)",
            stock_code,
            20,
        )
        lag_sec = self._trade_lag_seconds(trade_time_raw)
        normalized_code = registered_code_to_normalized.get(
            received_code, _stock_code(stock_code)
        )
        self._record_trade_lag(normalized_code, lag_sec)
        if lag_sec is None or lag_sec <= self._stale_trade_drop_seconds:
            return None
        trade_time_seconds = self._trade_time_seconds(trade_time_raw)
        with self._lock:
            last_accepted_seconds = self._last_accepted_trade_time_by_code.get(
                normalized_code
            )
            older_than_last_accepted = (
                trade_time_seconds is not None
                and last_accepted_seconds is not None
                and trade_time_seconds < last_accepted_seconds
            )
        registered_code = received_code if received_code in registered_codes else None
        sample = {
            "stock_code": normalized_code,
            "received_code": received_code,
            "normalized_code": normalized_code,
            "registered_code": registered_code,
            "original_registered_code": original_registered_codes.get(
                normalized_code
            ),
            "realtime_source_code": registered_code,
            "source_code": registered_code,
            "real_type": real_type,
            "trade_time_raw": trade_time_raw,
            "stale_trade_dropped": True,
            "stale_trade_drop_reason": (
                "older_than_last_accepted_and_lagged"
                if older_than_last_accepted
                else "lag_exceeds_drop_stale_trade_seconds"
            ),
            "trade_lag_sec": lag_sec,
        }
        with self._lock:
            self._stale_trade_drop_count += 1
            self._latest_only_dropped_count += 1
            if older_than_last_accepted:
                self._older_trade_drop_count += 1
            else:
                self._stale_trade_suspect_count += 1
                self._last_stale_trade_suspect_lag_sec = lag_sec
            self._last_stale_trade_lag_sec = lag_sec
        return sample

    def _expected_registration_batches(self, codes):
        for index in range(0, len(codes), self._REALREG_BATCH_SIZE):
            screen = str(
                self._EXPECTED_REALREG_SCREEN_START
                + index // self._REALREG_BATCH_SIZE
            )
            yield screen, codes[index : index + self._REALREG_BATCH_SIZE]

    def _after_single_registration_batches(self, codes):
        for index in range(0, len(codes), self._REALREG_BATCH_SIZE):
            screen = str(
                self._AFTER_SINGLE_REALREG_SCREEN_START
                + index // self._REALREG_BATCH_SIZE
            )
            yield screen, codes[index : index + self._REALREG_BATCH_SIZE]

    def _is_success_result(self, result):
        return result in (None, 0, "0")

    def _process_pending_realtime_requests(self):
        self._process_pending_unregister()
        self._process_pending_register()
        self._process_pending_orderbook_hot_refresh()

    def _process_pending_orderbook_hot_refresh(self):
        with self._lock:
            if not self._pending_orderbook_hot_refresh:
                return
            control = self._control
            login_state = self._login_state
            registered_codes = list(self._registered_code_order)
            if not registered_codes:
                registered_codes = list(self._registered_codes)
        if self._orderbook_mode == "off":
            with self._lock:
                self._pending_orderbook_hot_refresh = False
                self._orderbook_hot_refresh_error = "disabled_by_env"
            return
        if login_state == "requested":
            return
        if control is None or login_state != "connected":
            with self._lock:
                self._pending_orderbook_hot_refresh = False
                self._orderbook_hot_refresh_error = (
                    "QAx control is not available"
                    if control is None
                    else f"login_state={login_state}"
                )
            return
        if not registered_codes:
            with self._lock:
                self._pending_orderbook_hot_refresh = False
                self._orderbook_hot_refresh_error = "no_registered_codes"
            return

        orderbook_hot_codes, orderbook_rotate_pool = self._orderbook_groups(
            registered_codes
        )
        try:
            self._disconnect_realdata_screen(self._ORDERBOOK_HOT_SCREEN)
            if orderbook_hot_codes:
                self._register_orderbook_screen(
                    self._ORDERBOOK_HOT_SCREEN, orderbook_hot_codes
                )
            with self._lock:
                self._pending_orderbook_hot_refresh = False
                self._orderbook_hot_refresh_completed_at = (
                    datetime.now().isoformat(timespec="seconds")
                )
                self._orderbook_hot_refresh_error = None
                self._orderbook_realreg_requested = True
                self._orderbook_realreg_succeeded = True
                self._orderbook_realreg_error = None
                if self._ORDERBOOK_HOT_SCREEN not in self._orderbook_realreg_screens:
                    self._orderbook_realreg_screens = list(
                        self._orderbook_realreg_screens
                    ) + [self._ORDERBOOK_HOT_SCREEN]
                self._orderbook_realreg_screen_count = len(
                    self._orderbook_realreg_screens
                )
                self._orderbook_realreg_code_count = len(
                    set(orderbook_hot_codes) | set(self._orderbook_current_rotate_codes)
                )
                self._orderbook_hot_codes = list(orderbook_hot_codes)
                self._orderbook_rotate_pool = list(orderbook_rotate_pool)
                self._orderbook_last_rotate_at = None
                self._orderbook_last_rotate_at_text = None
                self._orderbook_registered_count = len(
                    set(orderbook_hot_codes) | set(self._orderbook_current_rotate_codes)
                )
        except Exception as error:
            with self._lock:
                self._pending_orderbook_hot_refresh = False
                self._orderbook_hot_refresh_error = str(error)
                self._orderbook_realreg_succeeded = False
                self._orderbook_realreg_error = str(error)
                self._last_error = f"orderbook hot refresh failed: {error}"

    def _process_pending_unregister(self):
        with self._lock:
            if not self._pending_unregister:
                return
            control = self._control
            screens = list(self._realreg_screens) + list(
                self._orderbook_realreg_screens
            ) + list(
                self._expected_realreg_screens
            ) + list(
                self._after_single_realreg_screens
            ) + list(
                self._suffix_realreg_screens
            )
            self._pending_unregister = False

        error_message = None
        if control is None:
            error_message = "QAx control is not available"
        else:
            try:
                for screen in screens:
                    control.dynamicCall("DisconnectRealData(QString)", screen)
            except Exception as error:
                error_message = str(error)

        with self._lock:
            if error_message is None:
                self._registered_codes.clear()
                self._registered_code_to_normalized.clear()
                self._registered_code_order = []
                self._realreg_succeeded = False
                self._realreg_screen_count = 0
                self._realreg_code_count = 0
                self._realreg_screens = []
                self._orderbook_realreg_succeeded = False
                self._orderbook_realreg_screen_count = 0
                self._orderbook_realreg_code_count = 0
                self._orderbook_realreg_screens = []
                self._expected_realreg_succeeded = False
                self._expected_realreg_screen_count = 0
                self._expected_realreg_code_count = 0
                self._expected_realreg_screens = []
                self._after_single_realreg_succeeded = False
                self._after_single_realreg_screen_count = 0
                self._after_single_realreg_code_count = 0
                self._after_single_realreg_screens = []
                self._suffix_realreg_requested = False
                self._suffix_realreg_succeeded = False
                self._suffix_realreg_screens = []
                self._suffix_realreg_codes = []
                self._suffix_sample_codes = set()
                self._suffix_store_skip_codes = set()
                self._unregister_succeeded = True
                self._unregister_error = None
            else:
                self._unregister_succeeded = False
                self._unregister_error = error_message
                self._last_error = f"DisconnectRealData failed: {error_message}"

    def _process_pending_register(self):
        with self._lock:
            pending_codes = self._pending_register_codes
            control = self._control
            login_state = self._login_state
        if not pending_codes:
            return
        if login_state == "requested":
            return

        with self._lock:
            self._pending_register_codes = None

        if control is None:
            self._mark_realreg_failed("QAx control is not available")
            return
        if login_state != "connected":
            self._mark_realreg_failed(f"login_state={login_state}")
            return

        codes = self._limited_realtime_codes(pending_codes)
        screens = []
        orderbook_screens = []
        orderbook_realtime_enabled = self._orderbook_mode != "off"
        orderbook_hot_codes, orderbook_rotate_pool = self._orderbook_groups(codes)
        orderbook_rotate_codes = (
            orderbook_rotate_pool[: self._orderbook_rotate_batch]
            if self._orderbook_mode == "hybrid"
            else []
        )
        diagnostic_realreg_enabled = (
            os.getenv("STOCKBOARD_ENABLE_DIAGNOSTIC_REALREG", "")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        suffix_realreg_enabled = (
            os.getenv("STOCKBOARD_ENABLE_SUFFIX_REALREG_EXPERIMENT", "")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        expected_screens = []
        expected_error = None
        after_single_screens = []
        after_single_error = None
        suffix_screens = []
        suffix_error = None
        suffix_registered_codes = [
            code
            for _, _, group_codes in self._SUFFIX_REALREG_GROUPS
            for code in group_codes
        ]
        suffix_sample_codes = suffix_registered_codes + list(
            self._SUFFIX_OPERATING_AL_CODES
        )
        try:
            for screen, batch in self._registration_batches(codes):
                screens.append(screen)
                result = control.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    screen,
                    ";".join(batch),
                    self._REALREG_FIDS,
                    "0",
                )
                if not self._is_success_result(result):
                    raise RuntimeError(
                        f"SetRealReg screen {screen} returned {result!r}"
                    )
            if orderbook_realtime_enabled:
                if orderbook_hot_codes:
                    self._register_orderbook_screen(
                        self._ORDERBOOK_HOT_SCREEN, orderbook_hot_codes
                    )
                    orderbook_screens.append(self._ORDERBOOK_HOT_SCREEN)
                if orderbook_rotate_codes:
                    self._register_orderbook_screen(
                        self._ORDERBOOK_ROTATE_SCREEN, orderbook_rotate_codes
                    )
                    orderbook_screens.append(self._ORDERBOOK_ROTATE_SCREEN)
            else:
                self._orderbook_seen_codes.clear()
                if self.store is not None:
                    try:
                        self.store.clear_orderbook_events()
                    except AttributeError:
                        pass
                    except Exception as error:
                        self._last_error = (
                            f"RealtimeStore.clear_orderbook_events failed: {error}"
                        )
        except Exception as error:
            self._mark_realreg_failed(str(error))
            return

        if diagnostic_realreg_enabled:
            for screen, batch in self._expected_registration_batches(codes):
                try:
                    result = control.dynamicCall(
                        "SetRealReg(QString, QString, QString, QString)",
                        screen,
                        ";".join(batch),
                        self._EXPECTED_REALREG_FIDS,
                        "0",
                    )
                    if not self._is_success_result(result):
                        raise RuntimeError(
                            f"SetRealReg expected screen {screen} returned {result!r}"
                        )
                    expected_screens.append(screen)
                except Exception as error:
                    expected_error = str(error)
                    break

            for screen, batch in self._after_single_registration_batches(codes):
                try:
                    result = control.dynamicCall(
                        "SetRealReg(QString, QString, QString, QString)",
                        screen,
                        ";".join(batch),
                        self._AFTER_SINGLE_REALREG_FIDS,
                        "0",
                    )
                    if not self._is_success_result(result):
                        raise RuntimeError(
                            "SetRealReg after-single screen "
                            f"{screen} returned {result!r}"
                        )
                    after_single_screens.append(screen)
                except Exception as error:
                    after_single_error = str(error)
                    break

        if suffix_realreg_enabled:
            for _, screen, group_codes in self._SUFFIX_REALREG_GROUPS:
                try:
                    result = control.dynamicCall(
                        "SetRealReg(QString, QString, QString, QString)",
                        screen,
                        ";".join(group_codes),
                        self._SUFFIX_REALREG_FIDS,
                        "0",
                    )
                    if not self._is_success_result(result):
                        raise RuntimeError(
                            f"SetRealReg suffix screen {screen} returned {result!r}"
                        )
                    suffix_screens.append(screen)
                except Exception as error:
                    suffix_error = str(error)
                    break

        with self._lock:
            self._registered_codes = set(codes)
            self._registered_code_order = list(codes)
            self._realreg_succeeded = True
            self._realreg_error = None
            self._realreg_screen_count = len(screens)
            self._realreg_code_count = len(codes)
            self._realreg_fids = self._REALREG_FIDS
            self._realreg_real_type = self._REALREG_REAL_TYPE
            self._realreg_screens = screens
            self._orderbook_realreg_requested = orderbook_realtime_enabled
            self._orderbook_realreg_succeeded = orderbook_realtime_enabled
            self._orderbook_realreg_error = (
                None if orderbook_realtime_enabled else "disabled_by_env"
            )
            self._orderbook_realreg_screen_count = len(orderbook_screens)
            self._orderbook_realreg_code_count = len(codes)
            self._orderbook_realreg_fids = self._ORDERBOOK_REALREG_FIDS
            self._orderbook_realreg_real_type = self._ORDERBOOK_REAL_TYPE
            self._orderbook_realreg_screens = orderbook_screens
            self._orderbook_hot_codes = list(orderbook_hot_codes)
            self._orderbook_rotate_pool = list(orderbook_rotate_pool)
            self._orderbook_current_rotate_codes = list(orderbook_rotate_codes)
            self._orderbook_next_rotate_index = len(orderbook_rotate_codes)
            self._orderbook_last_rotate_at = (
                time.monotonic() if orderbook_rotate_codes else None
            )
            self._orderbook_last_rotate_at_text = (
                datetime.now().isoformat(timespec="seconds")
                if orderbook_rotate_codes
                else None
            )
            self._orderbook_registered_count = len(
                set(orderbook_hot_codes) | set(orderbook_rotate_codes)
            )
            self._strength_5m_queue_size = len(codes)
            self._expected_realreg_succeeded = (
                diagnostic_realreg_enabled and expected_error is None
            )
            self._expected_realreg_error = expected_error
            self._expected_realreg_screen_count = len(expected_screens)
            self._expected_realreg_code_count = len(codes)
            self._expected_realreg_fids = self._EXPECTED_REALREG_FIDS
            self._expected_realreg_real_type = self._EXPECTED_REAL_TYPE
            self._expected_realreg_screens = expected_screens
            self._after_single_realreg_succeeded = (
                diagnostic_realreg_enabled and after_single_error is None
            )
            self._after_single_realreg_error = after_single_error
            self._after_single_realreg_screen_count = len(after_single_screens)
            self._after_single_realreg_code_count = len(codes)
            self._after_single_realreg_fids = self._AFTER_SINGLE_REALREG_FIDS
            self._after_single_realreg_real_type = self._AFTER_SINGLE_REAL_TYPE
            self._after_single_realreg_screens = after_single_screens
            self._suffix_realreg_requested = suffix_realreg_enabled
            self._suffix_realreg_succeeded = (
                suffix_realreg_enabled and suffix_error is None
            )
            self._suffix_realreg_error = suffix_error
            self._suffix_realreg_screens = suffix_screens
            self._suffix_realreg_codes = (
                suffix_sample_codes if suffix_realreg_enabled else []
            )
            self._suffix_realreg_fids = self._SUFFIX_REALREG_FIDS
            self._suffix_sample_codes = (
                set(suffix_sample_codes) if suffix_realreg_enabled else set()
            )
            self._suffix_store_skip_codes = (
                set(suffix_registered_codes) if suffix_realreg_enabled else set()
            )

    def _mark_realreg_failed(self, error_message):
        with self._lock:
            self._realreg_succeeded = False
            self._realreg_error = error_message
            self._orderbook_realreg_succeeded = False
            self._orderbook_realreg_error = error_message
            self._last_error = f"SetRealReg failed: {error_message}"

    def register_codes(self, codes):
        if isinstance(codes, str):
            codes = [codes]
        register_codes = []
        normalized_codes = []
        seen_register_codes = set()
        original_codes = {}
        register_to_normalized = {}
        input_codes = list(codes)
        code_map_sample = []
        for stock_code in codes:
            register_code = str(stock_code).strip()
            code = _stock_code(stock_code)
            if code is None:
                raise ValueError(f"invalid stock code: {stock_code!r}")
            if register_code and register_code not in seen_register_codes:
                register_codes.append(register_code)
                normalized_codes.append(code)
                seen_register_codes.add(register_code)
                original_codes[code] = register_code
                register_to_normalized[register_code] = code
                if len(code_map_sample) < 20:
                    code_map_sample.append(
                        {
                            "input_code": register_code,
                            "normalized_code": code,
                            "setrealreg_code": register_code,
                        }
                    )
        register_codes = self._limited_realtime_codes(register_codes)
        limited_register_set = set(register_codes)
        normalized_codes = [
            register_to_normalized[register_code]
            for register_code in register_codes
            if register_code in register_to_normalized
        ]
        original_codes = {
            code: register_code
            for code, register_code in original_codes.items()
            if register_code in limited_register_set
        }
        register_to_normalized = {
            register_code: code
            for register_code, code in register_to_normalized.items()
            if register_code in limited_register_set
        }
        code_map_sample = [
            item
            for item in code_map_sample
            if item["input_code"] in limited_register_set
        ][:20]
        screens = [
            screen
            for screen, _ in self._registration_batches(register_codes)
        ]
        orderbook_hot_codes, orderbook_rotate_pool = self._orderbook_groups(
            register_codes
        )
        orderbook_rotate_codes = (
            orderbook_rotate_pool[: self._orderbook_rotate_batch]
            if self._orderbook_mode == "hybrid"
            else []
        )
        orderbook_screens = []
        if self._orderbook_mode != "off":
            if orderbook_hot_codes:
                orderbook_screens.append(self._ORDERBOOK_HOT_SCREEN)
            if orderbook_rotate_codes:
                orderbook_screens.append(self._ORDERBOOK_ROTATE_SCREEN)
        diagnostic_realreg_enabled = (
            os.getenv("STOCKBOARD_ENABLE_DIAGNOSTIC_REALREG", "")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        expected_screens = (
            [
                screen
                for screen, _ in self._expected_registration_batches(register_codes)
            ]
            if diagnostic_realreg_enabled
            else []
        )
        after_single_screens = (
            [
                screen
                for screen, _ in self._after_single_registration_batches(register_codes)
            ]
            if diagnostic_realreg_enabled
            else []
        )
        with self._lock:
            self._registered_codes = set(register_codes)
            self._registered_code_order = list(register_codes)
            self._registered_code_to_normalized = dict(register_to_normalized)
            self._original_registered_codes = dict(original_codes)
            self._pending_register_codes = tuple(register_codes)
            self._register_input_codes_sample = [
                str(code) for code in input_codes[:20]
            ]
            self._register_normalized_codes_sample = normalized_codes[:20]
            self._setrealreg_codes_sample = register_codes[:20]
            self._register_code_map_sample = code_map_sample
            self._realreg_requested = True
            self._realreg_succeeded = False
            self._realreg_error = None
            self._realreg_screen_count = len(screens)
            self._realreg_code_count = len(register_codes)
            self._realreg_fids = self._REALREG_FIDS
            self._realreg_real_type = self._REALREG_REAL_TYPE
            self._realreg_screens = screens
            self._orderbook_realreg_requested = self._orderbook_mode != "off"
            self._orderbook_realreg_succeeded = False
            self._orderbook_realreg_error = (
                None if self._orderbook_mode != "off" else "disabled_by_env"
            )
            self._orderbook_realreg_screen_count = len(orderbook_screens)
            self._orderbook_realreg_code_count = len(
                set(orderbook_hot_codes) | set(orderbook_rotate_codes)
            )
            self._orderbook_realreg_fids = self._ORDERBOOK_REALREG_FIDS
            self._orderbook_realreg_real_type = self._ORDERBOOK_REAL_TYPE
            self._orderbook_realreg_screens = orderbook_screens
            self._orderbook_hot_codes = list(orderbook_hot_codes)
            self._orderbook_rotate_pool = list(orderbook_rotate_pool)
            self._orderbook_current_rotate_codes = list(orderbook_rotate_codes)
            self._orderbook_registered_count = len(
                set(orderbook_hot_codes) | set(orderbook_rotate_codes)
            )
            self._strength_5m_queue_size = len(register_codes)
            self._expected_realreg_succeeded = False
            self._expected_realreg_error = None
            self._expected_realreg_screen_count = len(expected_screens)
            self._expected_realreg_code_count = len(register_codes)
            self._expected_realreg_fids = self._EXPECTED_REALREG_FIDS
            self._expected_realreg_real_type = self._EXPECTED_REAL_TYPE
            self._expected_realreg_screens = expected_screens
            self._after_single_realreg_succeeded = False
            self._after_single_realreg_error = None
            self._after_single_realreg_screen_count = len(after_single_screens)
            self._after_single_realreg_code_count = len(register_codes)
            self._after_single_realreg_fids = self._AFTER_SINGLE_REALREG_FIDS
            self._after_single_realreg_real_type = self._AFTER_SINGLE_REAL_TYPE
            self._after_single_realreg_screens = after_single_screens
            return len(register_codes)

    def unregister_all(self):
        with self._lock:
            self._pending_unregister = True
            self._unregister_requested = True
            self._unregister_succeeded = False
            self._unregister_error = None
            return True

    def is_available(self):
        with self._lock:
            return self._check_availability()

    def status(self):
        with self._lock:
            recent_lags = list(self._recent_trade_lags)
            avg_trade_lag = (
                round(sum(recent_lags) / len(recent_lags), 3)
                if recent_lags
                else None
            )
            strength_ready, strength_ready_reason = (
                self._strength_probe_ready_state_locked()
            )
            now = time.monotonic()
            pending_items = list(self._strength_probe_pending)
            deferred_items = [
                item
                for item in pending_items
                if (item.get("next_retry_at_monotonic") or 0) > now
                or strength_ready is not True
            ]
            orderbook_ready, orderbook_ready_reason = (
                self._orderbook_probe_ready_state_locked()
            )
            orderbook_pending_items = list(self._orderbook_probe_pending)
            orderbook_deferred_items = [
                item
                for item in orderbook_pending_items
                if (item.get("next_retry_at_monotonic") or 0) > now
                or orderbook_ready is not True
            ]
            store_latest_only = {}
            if self.store is not None and hasattr(
                self.store, "latest_only_diagnostics"
            ):
                try:
                    store_latest_only = self.store.latest_only_diagnostics()
                except Exception:
                    store_latest_only = {}
            store_guard_drop_count = store_latest_only.get(
                "store_update_guard_drop_count", 0
            )
            store_one_min_bucket = {}
            if self.store is not None and hasattr(
                self.store, "one_min_bucket_diagnostics"
            ):
                try:
                    store_one_min_bucket = (
                        self.store.one_min_bucket_diagnostics()
                    )
                except Exception:
                    store_one_min_bucket = {}
            status = {
                "available": self._check_availability(),
                "running": self._running,
                "registered_count": len(self._registered_codes),
                "display_mode": self._display_mode,
                "price_fast_mode": self._price_fast_mode,
                "price_light_lane_enabled": self._price_light_lane_enabled,
                "price_light_lane_priority": "below_hot",
                "price_light_lane_fids": "10;12",
                "price_light_top_limit": self._price_light_top_limit,
                "price_light_min_interval_sec": (
                    self._price_light_min_interval_sec
                ),
                "realtime_code_limit": self._realtime_code_limit,
                "orderbook_realtime_enabled": self._orderbook_realtime_enabled,
                "orderbook_mode": self._orderbook_mode,
                "orderbook_hot_source": self._orderbook_hot_source,
                "orderbook_hot_limit": self._orderbook_hot_limit,
                "hot_priority_codes_sample": list(
                    self._hot_priority_codes[:20]
                ),
                "orderbook_rotate_batch": self._orderbook_rotate_batch,
                "orderbook_rotate_interval_sec": (
                    self._orderbook_rotate_interval_sec
                ),
                "orderbook_display": self._orderbook_display,
                "orderbook_registered_count": self._orderbook_registered_count,
                "orderbook_hot_refresh_pending": (
                    self._pending_orderbook_hot_refresh
                ),
                "orderbook_hot_refresh_requested_at": (
                    self._orderbook_hot_refresh_requested_at
                ),
                "orderbook_hot_refresh_completed_at": (
                    self._orderbook_hot_refresh_completed_at
                ),
                "orderbook_hot_refresh_error": (
                    self._orderbook_hot_refresh_error
                ),
                "orderbook_hot_codes_sample": list(
                    self._orderbook_hot_codes[:20]
                ),
                "orderbook_rotate_codes_sample": list(
                    self._orderbook_current_rotate_codes[:20]
                ),
                "orderbook_last_rotate_at": self._orderbook_last_rotate_at_text,
                "strength_5m_enabled": self._strength_5m_enabled,
                "strength_5m_queue_size": self._strength_5m_queue_size,
                "strength_5m_last_cycle_at": self._strength_5m_last_cycle_at,
                "close_metrics_queue_size": len(self._close_metrics_queue),
                "close_metrics_cache_size": len(self._close_metrics_cache),
                "strength_probe_cache_size": len(self._strength_probe_cache),
                "close_metrics_last_cycle_at": self._close_metrics_last_cycle_at,
                "close_metrics_last_error": self._close_metrics_last_error,
                "close_metrics_rate_limit_per_sec": round(
                    1 / self._close_metrics_query_interval_sec, 3
                ),
                "close_metrics_tr_notes": dict(self._close_metrics_tr_notes),
                "tr_event_connected": self._tr_event_connected,
                "strength_probe_ready": strength_ready,
                "provider_ready_reason": strength_ready_reason,
                "provider_not_ready_reason": (
                    None if strength_ready else strength_ready_reason
                ),
                "strength_probe_pending_count": len(self._strength_probe_pending),
                "strength_probe_pending_sample": [
                    item.get("stock_code")
                    for item in pending_items[:20]
                ],
                "strength_probe_deferred_count": len(deferred_items),
                "strength_probe_deferred_sample": [
                    item.get("stock_code")
                    for item in deferred_items[:20]
                ],
                "strength_probe_inflight_code": (
                    self._strength_probe_inflight.get("stock_code")
                    if self._strength_probe_inflight
                    else None
                ),
                "strength_probe_last_result": (
                    dict(self._strength_probe_last_result)
                    if isinstance(self._strength_probe_last_result, dict)
                    else self._strength_probe_last_result
                ),
                "strength_probe_last_error": self._strength_probe_last_error,
                "strength_probe_last_raw_sample": list(
                    self._strength_probe_last_raw_sample
                ),
                "orderbook_probe_ready": orderbook_ready,
                "orderbook_probe_ready_reason": orderbook_ready_reason,
                "orderbook_probe_not_ready_reason": (
                    None if orderbook_ready else orderbook_ready_reason
                ),
                "orderbook_probe_pending_count": len(self._orderbook_probe_pending),
                "orderbook_probe_pending_sample": [
                    item.get("stock_code")
                    for item in orderbook_pending_items[:20]
                ],
                "orderbook_probe_deferred_count": len(orderbook_deferred_items),
                "orderbook_probe_deferred_sample": [
                    item.get("stock_code")
                    for item in orderbook_deferred_items[:20]
                ],
                "orderbook_probe_inflight_code": (
                    self._orderbook_probe_inflight.get("stock_code")
                    if self._orderbook_probe_inflight
                    else None
                ),
                "orderbook_probe_cache_size": len(self._orderbook_probe_cache),
                "orderbook_probe_last_result": (
                    dict(self._orderbook_probe_last_result)
                    if isinstance(self._orderbook_probe_last_result, dict)
                    else self._orderbook_probe_last_result
                ),
                "orderbook_probe_last_error": self._orderbook_probe_last_error,
                "orderbook_probe_last_raw_sample": list(
                    self._orderbook_probe_last_raw_sample
                ),
                "latest_only_enabled": self._latest_only_enabled,
                "trade_event_received_count": self._trade_event_received_count,
                "trade_event_applied_count": self._trade_event_applied_count,
                "stale_trade_drop_seconds": self._stale_trade_drop_seconds,
                "stale_trade_drop_count": self._stale_trade_drop_count,
                "stale_trade_suspect_count": self._stale_trade_suspect_count,
                "older_trade_drop_count": self._older_trade_drop_count
                + int(store_latest_only.get("older_trade_drop_count") or 0),
                "last_stale_trade_lag_sec": self._last_stale_trade_lag_sec,
                "last_stale_trade_suspect_lag_sec": (
                    self._last_stale_trade_suspect_lag_sec
                ),
                "last_trade_lag_sec": self._last_trade_lag_sec,
                "max_trade_lag_sec": self._max_trade_lag_sec,
                "avg_trade_lag_sec_recent": avg_trade_lag,
                "latest_only_dropped_count": (
                    self._latest_only_dropped_count
                    + int(
                        store_latest_only.get("latest_only_dropped_count") or 0
                    )
                ),
                "store_update_guard_drop_count": store_guard_drop_count,
                "latest_trade_lag_by_code_sample": dict(
                    list(self._latest_trade_lag_by_code.items())[:20]
                ),
                "last_error": self._last_error,
                "last_received_at": self._last_received_at,
                "qt_ready": self._qt_ready,
                "control_created": self._control_created,
                "login_requested": self._login_requested,
                "login_state": self._login_state,
                "login_error_code": self._login_error_code,
                "login_completed_at": self._login_completed_at,
                "qt_pump_running": self._qt_pump_running,
                "qt_pump_last_at": self._qt_pump_last_at,
                "realreg_requested": self._realreg_requested,
                "realreg_succeeded": self._realreg_succeeded,
                "realreg_error": self._realreg_error,
                "realreg_screen_count": self._realreg_screen_count,
                "realreg_code_count": self._realreg_code_count,
                "realreg_fids": self._realreg_fids,
                "realreg_real_type": self._realreg_real_type,
                "realreg_screens": list(self._realreg_screens),
                "register_input_codes_sample": list(
                    self._register_input_codes_sample
                ),
                "register_normalized_codes_sample": list(
                    self._register_normalized_codes_sample
                ),
                "setrealreg_codes_sample": list(self._setrealreg_codes_sample),
                "register_code_map_sample": list(
                    self._register_code_map_sample
                ),
                "orderbook_realreg_requested": self._orderbook_realreg_requested,
                "orderbook_realreg_succeeded": self._orderbook_realreg_succeeded,
                "orderbook_realreg_error": self._orderbook_realreg_error,
                "orderbook_realreg_screen_count": (
                    self._orderbook_realreg_screen_count
                ),
                "orderbook_realreg_code_count": self._orderbook_realreg_code_count,
                "orderbook_realreg_fids": self._orderbook_realreg_fids,
                "orderbook_realreg_real_type": self._orderbook_realreg_real_type,
                "orderbook_realreg_screens": list(
                    self._orderbook_realreg_screens
                ),
                "expected_realreg_succeeded": self._expected_realreg_succeeded,
                "expected_realreg_error": self._expected_realreg_error,
                "expected_realreg_screen_count": (
                    self._expected_realreg_screen_count
                ),
                "expected_realreg_code_count": self._expected_realreg_code_count,
                "expected_realreg_fids": self._expected_realreg_fids,
                "expected_realreg_real_type": self._expected_realreg_real_type,
                "expected_realreg_screens": list(self._expected_realreg_screens),
                "after_single_realreg_succeeded": (
                    self._after_single_realreg_succeeded
                ),
                "after_single_realreg_error": self._after_single_realreg_error,
                "after_single_realreg_screen_count": (
                    self._after_single_realreg_screen_count
                ),
                "after_single_realreg_code_count": (
                    self._after_single_realreg_code_count
                ),
                "after_single_realreg_fids": self._after_single_realreg_fids,
                "after_single_realreg_real_type": (
                    self._after_single_realreg_real_type
                ),
                "after_single_realreg_screens": list(
                    self._after_single_realreg_screens
                ),
                "suffix_realreg_requested": self._suffix_realreg_requested,
                "suffix_realreg_succeeded": self._suffix_realreg_succeeded,
                "suffix_realreg_error": self._suffix_realreg_error,
                "suffix_realreg_screens": list(self._suffix_realreg_screens),
                "suffix_realreg_codes": list(self._suffix_realreg_codes),
                "suffix_realreg_fids": self._suffix_realreg_fids,
                "suffix_last_samples": dict(self._suffix_last_samples),
                "realdata_received_count": self._realdata_received_count,
                "realdata_last_received_at": self._realdata_last_received_at,
                "realdata_last_code": self._realdata_last_code,
                "realdata_last_real_type": self._realdata_last_real_type,
                "realdata_last_sample": self._realdata_last_sample,
                "realdata_parse_error": self._realdata_parse_error,
                "last_received_code": self._last_received_code,
                "last_normalized_code": self._last_normalized_code,
                "last_registered_code": self._last_registered_code,
                "last_original_registered_code": (
                    self._last_original_registered_code
                ),
                "last_fid10_raw": self._last_fid10_raw,
                "last_fid20_raw": self._last_fid20_raw,
                "trade_last_sample": self._trade_last_sample,
                "trade_last_received_code": self._trade_last_received_code,
                "trade_last_normalized_code": self._trade_last_normalized_code,
                "trade_last_fid10_raw": self._trade_last_fid10_raw,
                "trade_last_fid20_raw": self._trade_last_fid20_raw,
                "trade_last_received_at": self._trade_last_received_at,
                "orderbook_last_sample": self._orderbook_last_sample,
                "orderbook_last_received_code": (
                    self._orderbook_last_received_code
                ),
                "orderbook_last_normalized_code": (
                    self._orderbook_last_normalized_code
                ),
                "orderbook_last_received_at": self._orderbook_last_received_at,
                "ecn_orderbook_last_sample": self._ecn_orderbook_last_sample,
                "ecn_orderbook_last_received_code": (
                    self._ecn_orderbook_last_received_code
                ),
                "ecn_orderbook_last_normalized_code": (
                    self._ecn_orderbook_last_normalized_code
                ),
                "ecn_orderbook_last_received_at": (
                    self._ecn_orderbook_last_received_at
                ),
                "ecn_orderbook_seen_codes_count": len(
                    self._ecn_orderbook_seen_codes
                ),
                "ecn_orderbook_seen_codes_sample": (
                    sorted(self._ecn_orderbook_seen_codes)[:20]
                ),
                "trade_seen_codes_count": len(self._trade_seen_codes),
                "trade_seen_codes_sample": sorted(self._trade_seen_codes)[:20],
                "orderbook_seen_codes_count": len(self._orderbook_seen_codes),
                "orderbook_seen_codes_sample": (
                    sorted(self._orderbook_seen_codes)[:20]
                ),
                "expected_last_sample": self._expected_last_sample,
                "expected_last_received_code": self._expected_last_received_code,
                "expected_last_received_at": self._expected_last_received_at,
                "after_single_last_sample": self._after_single_last_sample,
                "after_single_last_received_code": (
                    self._after_single_last_received_code
                ),
                "after_single_last_received_at": (
                    self._after_single_last_received_at
                ),
                "trade_fid290_raw": self._trade_fid290_raw,
                "unregister_requested": self._unregister_requested,
                "unregister_succeeded": self._unregister_succeeded,
                "unregister_error": self._unregister_error,
            }
            status.update(store_one_min_bucket)
            return status

    def _on_receive_real_data(self, *args, **kwargs):
        received_at = datetime.now().isoformat(timespec="seconds")
        stock_code = args[0] if len(args) > 0 else kwargs.get("stock_code")
        real_type = args[1] if len(args) > 1 else kwargs.get("real_type")
        received_code = "" if stock_code is None else str(stock_code)
        normalized_code = _stock_code(stock_code)
        sample = None
        parse_error = None
        if real_type == self._REALREG_REAL_TYPE:
            try:
                with self._lock:
                    is_suffix_sample_code = received_code in self._suffix_sample_codes
                    is_suffix_store_skip_code = (
                        received_code in self._suffix_store_skip_codes
                    )
                if is_suffix_sample_code:
                    suffix_sample = self._parse_suffix_real_data(
                        stock_code, real_type, received_code, received_at
                    )
                    with self._lock:
                        self._suffix_last_samples[received_code] = suffix_sample
                    sample = suffix_sample
                if not is_suffix_store_skip_code:
                    stale_sample = self._stale_trade_drop_sample(
                        stock_code, real_type, received_code
                    )
                    if stale_sample is not None:
                        sample = stale_sample
                    else:
                        sample = self._parse_trade_real_data(stock_code, real_type)
                        trade_lag_sec = self._trade_lag_seconds(
                            sample.get("trade_time_raw")
                        )
                        sample["trade_lag_sec"] = trade_lag_sec
                        sample["fid20_trade_lag_sec"] = trade_lag_sec
                        if (
                            self._stale_trade_drop_seconds > 0
                            and trade_lag_sec is not None
                            and trade_lag_sec > self._stale_trade_drop_seconds
                        ):
                            sample["stale_trade_suspect"] = True
                        self._record_trade_lag(
                            sample.get("normalized_code"),
                            trade_lag_sec,
                        )
                        self._store_trade_tick(sample, received_at)
            except Exception as error:
                parse_error = f"{type(error).__name__}: {error}"
        elif real_type == self._ORDERBOOK_REAL_TYPE:
            try:
                sample = self._parse_orderbook_real_data(stock_code, real_type)
                self._store_orderbook(sample, received_at)
            except Exception as error:
                parse_error = f"{type(error).__name__}: {error}"
        elif real_type == self._ECN_ORDERBOOK_REAL_TYPE:
            try:
                sample = self._parse_ecn_orderbook_real_data(
                    stock_code, real_type
                )
            except Exception as error:
                parse_error = f"{type(error).__name__}: {error}"
        elif real_type == self._EXPECTED_REAL_TYPE:
            try:
                sample = self._parse_diagnostic_real_data(stock_code, real_type)
            except Exception as error:
                parse_error = f"{type(error).__name__}: {error}"
        elif real_type == self._AFTER_SINGLE_REAL_TYPE:
            try:
                sample = self._parse_diagnostic_real_data(stock_code, real_type)
            except Exception as error:
                parse_error = f"{type(error).__name__}: {error}"
        with self._lock:
            self._realdata_received_count += 1
            if real_type == self._REALREG_REAL_TYPE:
                self._trade_event_received_count += 1
            self._realdata_last_received_at = received_at
            self._realdata_last_code = normalized_code
            self._realdata_last_real_type = real_type
            if sample is not None:
                self._realdata_last_sample = sample
            self._last_received_code = "" if stock_code is None else str(stock_code)
            self._last_normalized_code = normalized_code
            self._last_registered_code = (
                received_code if received_code in self._registered_codes else None
            )
            self._last_original_registered_code = (
                self._original_registered_codes.get(normalized_code)
                if normalized_code is not None
                else None
            )
            if sample is not None and "price_raw" in sample:
                self._last_fid10_raw = sample.get("price_raw")
            if sample is not None and "trade_time_raw" in sample:
                self._last_fid20_raw = sample.get("trade_time_raw")
            if sample is not None and real_type == self._REALREG_REAL_TYPE:
                self._trade_last_sample = sample
                self._trade_last_received_code = (
                    "" if stock_code is None else str(stock_code)
                )
                self._trade_last_normalized_code = normalized_code
                self._trade_last_fid10_raw = sample.get("price_raw")
                self._trade_last_fid20_raw = sample.get("trade_time_raw")
                self._trade_fid290_raw = sample.get("market_type_raw")
                self._trade_last_received_at = received_at
                if normalized_code is not None:
                    self._trade_seen_codes.add(normalized_code)
            elif sample is not None and real_type == self._ORDERBOOK_REAL_TYPE:
                self._orderbook_last_sample = sample
                self._orderbook_last_received_code = (
                    "" if stock_code is None else str(stock_code)
                )
                self._orderbook_last_normalized_code = normalized_code
                self._orderbook_last_received_at = received_at
                if normalized_code is not None:
                    self._orderbook_seen_codes.add(normalized_code)
            elif sample is not None and real_type == self._ECN_ORDERBOOK_REAL_TYPE:
                self._ecn_orderbook_last_sample = sample
                self._ecn_orderbook_last_received_code = (
                    "" if stock_code is None else str(stock_code)
                )
                self._ecn_orderbook_last_normalized_code = normalized_code
                self._ecn_orderbook_last_received_at = received_at
                if normalized_code is not None:
                    self._ecn_orderbook_seen_codes.add(normalized_code)
            elif sample is not None and real_type == self._EXPECTED_REAL_TYPE:
                self._expected_last_sample = sample
                self._expected_last_received_code = (
                    "" if stock_code is None else str(stock_code)
                )
                self._expected_last_received_at = received_at
            elif sample is not None and real_type == self._AFTER_SINGLE_REAL_TYPE:
                self._after_single_last_sample = sample
                self._after_single_last_received_code = (
                    "" if stock_code is None else str(stock_code)
                )
                self._after_single_last_received_at = received_at
            self._realdata_parse_error = parse_error
            self._last_received_at = received_at
        return {}

    def _parse_trade_real_data(self, stock_code, real_type):
        with self._lock:
            control = self._control
            registered_codes = set(self._registered_codes)
            original_registered_codes = dict(self._original_registered_codes)
            registered_code_to_normalized = dict(
                self._registered_code_to_normalized
            )
        if control is None:
            raise RuntimeError("QAx control is not available")

        received_code = "" if stock_code is None else str(stock_code)
        normalized_code = registered_code_to_normalized.get(
            received_code, _stock_code(stock_code)
        )
        registered_code = received_code if received_code in registered_codes else None
        sample = {
            "stock_code": normalized_code,
            "received_code": received_code,
            "normalized_code": normalized_code,
            "registered_code": registered_code,
            "original_registered_code": original_registered_codes.get(
                normalized_code
            ),
            "realtime_source_code": registered_code,
            "source_code": registered_code,
            "real_type": real_type,
        }
        for fid, key in self._TRADE_REALDATA_FIDS:
            sample[key] = control.dynamicCall(
                "GetCommRealData(QString, int)",
                stock_code,
                fid,
            )
        return sample

    def _parse_diagnostic_real_data(self, stock_code, real_type):
        with self._lock:
            control = self._control
            registered_codes = set(self._registered_codes)
            original_registered_codes = dict(self._original_registered_codes)
            registered_code_to_normalized = dict(
                self._registered_code_to_normalized
            )
        if control is None:
            raise RuntimeError("QAx control is not available")

        received_code = "" if stock_code is None else str(stock_code)
        normalized_code = registered_code_to_normalized.get(
            received_code, _stock_code(stock_code)
        )
        registered_code = received_code if received_code in registered_codes else None
        sample = {
            "stock_code": normalized_code,
            "received_code": received_code,
            "normalized_code": normalized_code,
            "registered_code": registered_code,
            "original_registered_code": original_registered_codes.get(
                normalized_code
            ),
            "realtime_source_code": registered_code,
            "source_code": registered_code,
            "real_type": real_type,
        }
        for fid, key in self._DIAGNOSTIC_REALDATA_FIDS:
            sample[key] = control.dynamicCall(
                "GetCommRealData(QString, int)",
                stock_code,
                fid,
            )
        return sample

    def _parse_suffix_real_data(
        self, stock_code, real_type, registered_code, received_at
    ):
        with self._lock:
            control = self._control
        if control is None:
            raise RuntimeError("QAx control is not available")

        received_code = "" if stock_code is None else str(stock_code)
        sample = {
            "registered_code": registered_code,
            "received_code": received_code,
            "normalized_code": _stock_code(stock_code),
            "real_type": real_type,
            "received_at": received_at,
        }
        for fid, key in self._SUFFIX_REALDATA_FIDS:
            sample[key] = control.dynamicCall(
                "GetCommRealData(QString, int)",
                stock_code,
                fid,
            )
        return sample

    @staticmethod
    def _realdata_number(value):
        if value in (None, ""):
            return None
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        if text.startswith("+"):
            text = text[1:]
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        if not number.is_finite():
            return None
        return int(number) if number == number.to_integral() else float(number)

    def _store_trade_tick(self, sample, received_at):
        stock_code = _stock_code(sample.get("stock_code"))
        if stock_code is None:
            raise RuntimeError(f"invalid stock code: {sample.get('stock_code')!r}")
        if self.store is None:
            raise RuntimeError("RealtimeStore is not available")

        tick = {
            "stock_code": stock_code,
            "received_code": sample.get("received_code"),
            "normalized_code": sample.get("normalized_code"),
            "registered_code": sample.get("registered_code"),
            "original_registered_code": sample.get("original_registered_code"),
            "realtime_source_code": sample.get("realtime_source_code"),
            "source_code": sample.get("source_code"),
            "price": normalize_kiwoom_price(sample.get("price_raw")),
            "change_rate": self._realdata_number(sample.get("change_rate_raw")),
            "trade_time": sample.get("trade_time_raw") or None,
            "trade_lag_sec": sample.get("trade_lag_sec"),
            "fid20_trade_lag_sec": sample.get("fid20_trade_lag_sec"),
            "stale_trade_suspect": sample.get("stale_trade_suspect"),
            "volume": self._realdata_number(sample.get("trade_qty_raw")),
            "strength": self._realdata_number(
                sample.get("execution_strength_raw")
            ),
            "acc_volume": self._realdata_number(
                sample.get("cumulative_volume_raw")
            ),
            "acc_trade_value": self._realdata_number(
                sample.get("cumulative_value_raw")
            ),
            "received_at": received_at,
            "raw": dict(sample),
        }
        try:
            guard_drop_count_before = None
            if hasattr(self.store, "latest_only_diagnostics"):
                guard_drop_count_before = self.store.latest_only_diagnostics().get(
                    "store_update_guard_drop_count"
                )
            self.store.update_trade(
                stock_code,
                price=tick["price"],
                change_rate=tick["change_rate"],
                trade_qty=tick["volume"],
                trade_time=tick["trade_time"],
                execution_strength=tick["strength"],
                cumulative_volume=tick["acc_volume"],
                cumulative_value=tick["acc_trade_value"],
                received_code=tick["received_code"],
                normalized_code=tick["normalized_code"],
                registered_code=tick["registered_code"],
                original_registered_code=tick["original_registered_code"],
                realtime_source_code=tick["realtime_source_code"],
                source_code=tick["source_code"],
                price_first=self._price_fast_mode,
                trade_lag_sec=tick["trade_lag_sec"],
                fid20_trade_lag_sec=tick["fid20_trade_lag_sec"],
                stale_trade_suspect=tick["stale_trade_suspect"],
                received_at=tick["received_at"],
            )
            guard_dropped = False
            if guard_drop_count_before is not None and hasattr(
                self.store, "latest_only_diagnostics"
            ):
                guard_drop_count_after = self.store.latest_only_diagnostics().get(
                    "store_update_guard_drop_count"
                )
                guard_dropped = guard_drop_count_after != guard_drop_count_before
            if not guard_dropped:
                with self._lock:
                    self._trade_event_applied_count += 1
            trade_time_seconds = self._trade_time_seconds(tick["trade_time"])
            if not guard_dropped and trade_time_seconds is not None:
                with self._lock:
                    previous_trade_time_seconds = (
                        self._last_accepted_trade_time_by_code.get(stock_code)
                    )
                    if (
                        previous_trade_time_seconds is None
                        or trade_time_seconds > previous_trade_time_seconds
                    ):
                        self._last_accepted_trade_time_by_code[stock_code] = (
                            trade_time_seconds
                        )
        except Exception as error:
            with self._lock:
                self._last_error = f"RealtimeStore.update_trade failed: {error}"
            raise

    def _parse_orderbook_real_data(self, stock_code, real_type):
        with self._lock:
            control = self._control
            registered_codes = set(self._registered_codes)
            original_registered_codes = dict(self._original_registered_codes)
            registered_code_to_normalized = dict(
                self._registered_code_to_normalized
            )
        if control is None:
            raise RuntimeError("QAx control is not available")

        received_code = "" if stock_code is None else str(stock_code)
        normalized_code = registered_code_to_normalized.get(
            received_code, _stock_code(stock_code)
        )
        registered_code = received_code if received_code in registered_codes else None
        sample = {
            "stock_code": normalized_code,
            "received_code": received_code,
            "normalized_code": normalized_code,
            "registered_code": registered_code,
            "original_registered_code": original_registered_codes.get(
                normalized_code
            ),
            "realtime_source_code": registered_code,
            "source_code": registered_code,
            "real_type": real_type,
        }
        with self._lock:
            if received_code in set(self._orderbook_hot_codes):
                sample["orderbook_source"] = "live_hot"
            elif received_code in set(self._orderbook_current_rotate_codes):
                sample["orderbook_source"] = "rotating"
            else:
                sample["orderbook_source"] = "unavailable"
        for fid, key in self._ORDERBOOK_REALDATA_FIDS:
            sample[key] = control.dynamicCall(
                "GetCommRealData(QString, int)",
                stock_code,
                fid,
            )
        return sample

    def _parse_ecn_orderbook_real_data(self, stock_code, real_type):
        with self._lock:
            control = self._control
        if control is None:
            raise RuntimeError("QAx control is not available")

        sample = {
            "stock_code": _stock_code(stock_code),
            "real_type": real_type,
        }
        for fid, key in self._ECN_ORDERBOOK_REALDATA_FIDS:
            sample[key] = control.dynamicCall(
                "GetCommRealData(QString, int)",
                stock_code,
                fid,
            )
        return sample

    def _store_orderbook(self, sample, received_at):
        stock_code = _stock_code(sample.get("stock_code"))
        if stock_code is None:
            raise RuntimeError(f"invalid stock code: {sample.get('stock_code')!r}")
        if self.store is None:
            raise RuntimeError("RealtimeStore is not available")

        orderbook = {
            "stock_code": stock_code,
            "received_code": sample.get("received_code"),
            "normalized_code": sample.get("normalized_code"),
            "registered_code": sample.get("registered_code"),
            "original_registered_code": sample.get("original_registered_code"),
            "realtime_source_code": sample.get("realtime_source_code"),
            "source_code": sample.get("source_code"),
            "orderbook_source": sample.get("orderbook_source"),
            "bid_volume": self._realdata_number(sample.get("bid_volume_raw")),
            "ask_volume": self._realdata_number(sample.get("ask_volume_raw")),
            "best_bid_price": normalize_kiwoom_price(
                sample.get("best_bid_price_raw")
            ),
            "best_ask_price": normalize_kiwoom_price(
                sample.get("best_ask_price_raw")
            ),
            "received_at": received_at,
            "raw": dict(sample),
        }
        try:
            self.store.update_orderbook(
                stock_code,
                orderbook,
                received_code=orderbook["received_code"],
                normalized_code=orderbook["normalized_code"],
                registered_code=orderbook["registered_code"],
                original_registered_code=orderbook["original_registered_code"],
                realtime_source_code=orderbook["realtime_source_code"],
                source_code=orderbook["source_code"],
                orderbook_source=orderbook.get("orderbook_source"),
            )
        except Exception as error:
            with self._lock:
                self._last_error = f"RealtimeStore.update_orderbook failed: {error}"
            raise

    def _parse_foreign_line_real_data(self, *args, **kwargs):
        return {}
