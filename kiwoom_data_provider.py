import json
import os
import sys
import time
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

    unique_rows = {}
    for row in collected_rows:
        stock_code = row["stock_code"]
        if stock_code and stock_code not in unique_rows:
            unique_rows[stock_code] = row

    rows = sorted(
        unique_rows.values(),
        key=lambda row: (
            row["trade_value_eok"] is not None,
            row["trade_value_eok"] if row["trade_value_eok"] is not None else 0,
        ),
        reverse=True,
    )[:300]
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
                stock_code = _stock_code(_first(row, "stk_cd", "stock_code", "종목코드"))
                raw_value = _first(
                    row,
                    "netprps_prica",
                    "program_net",
                    "프로그램순매수대금",
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
                "종합" in str(row.get("inds_nm", ""))
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
    _REALREG_REAL_TYPE = "주식체결"
    _ORDERBOOK_REALREG_SCREEN_START = 9010
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
    _ORDERBOOK_REAL_TYPE = "주식호가잔량"
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

            with self._lock:
                self._control = control
                self._control_created = True
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

        codes = list(pending_codes)
        screens = []
        orderbook_screens = []
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
            for screen, batch in self._orderbook_registration_batches(codes):
                orderbook_screens.append(screen)
                result = control.dynamicCall(
                    "SetRealReg(QString, QString, QString, QString)",
                    screen,
                    ";".join(batch),
                    self._ORDERBOOK_REALREG_FIDS,
                    "0",
                )
                if not self._is_success_result(result):
                    raise RuntimeError(
                        f"SetRealReg orderbook screen {screen} returned {result!r}"
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
            self._realreg_succeeded = True
            self._realreg_error = None
            self._realreg_screen_count = len(screens)
            self._realreg_code_count = len(codes)
            self._realreg_fids = self._REALREG_FIDS
            self._realreg_real_type = self._REALREG_REAL_TYPE
            self._realreg_screens = screens
            self._orderbook_realreg_requested = True
            self._orderbook_realreg_succeeded = True
            self._orderbook_realreg_error = None
            self._orderbook_realreg_screen_count = len(orderbook_screens)
            self._orderbook_realreg_code_count = len(codes)
            self._orderbook_realreg_fids = self._ORDERBOOK_REALREG_FIDS
            self._orderbook_realreg_real_type = self._ORDERBOOK_REAL_TYPE
            self._orderbook_realreg_screens = orderbook_screens
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
        screens = [
            screen
            for screen, _ in self._registration_batches(register_codes)
        ]
        orderbook_screens = [
            screen for screen, _ in self._orderbook_registration_batches(register_codes)
        ]
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
            self._orderbook_realreg_requested = True
            self._orderbook_realreg_succeeded = False
            self._orderbook_realreg_error = None
            self._orderbook_realreg_screen_count = len(orderbook_screens)
            self._orderbook_realreg_code_count = len(register_codes)
            self._orderbook_realreg_fids = self._ORDERBOOK_REALREG_FIDS
            self._orderbook_realreg_real_type = self._ORDERBOOK_REAL_TYPE
            self._orderbook_realreg_screens = orderbook_screens
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
            return {
                "available": self._check_availability(),
                "running": self._running,
                "registered_count": len(self._registered_codes),
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
                    sample = self._parse_trade_real_data(stock_code, real_type)
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
            )
        except Exception as error:
            with self._lock:
                self._last_error = f"RealtimeStore.update_orderbook failed: {error}"
            raise

    def _parse_foreign_line_real_data(self, *args, **kwargs):
        return {}
