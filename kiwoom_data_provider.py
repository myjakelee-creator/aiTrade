import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stockboard_engine import (
    _build_ohlc, _daily_row_date, _first, _market_flow_number, _million_to_eok,
    _normalize_row, _ohlc_row_sample, _program_net_eok, _program_net_eok_divisor,
    _recent_dates, _required_market_count, _required_market_number,
    _select_daily_rows, _stock_code,
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
                    rate_limit = {"market": market_name, "page": page_count + 1}
                    print(
                        f"warning: ka90004 HTTP 429 at market {market_name}, "
                        f"page {page_count + 1}; stopping program lookup",
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

        if market_error is None or rate_limit is not None:
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
            }
        )
        if rate_limit is not None:
            break

    return {
        "values": program_net_by_code,
        "market_stats": market_stats,
        "market_counts": market_counts,
        "raw_samples": raw_samples,
        "converted_samples": converted_samples,
        "divisor": divisor,
        "errors": errors,
        "rate_limit": rate_limit,
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


def fetch_market_supply(access_token, query_date):
    """Fetch raw KOSPI/KOSDAQ index and advance/decline statistics."""
    market_supply = {}
    markets = (
        ("kospi", "KOSPI", "0", "001", "P001_AL01"),
        ("kosdaq", "KOSDAQ", "1", "101", "P101_AL02"),
    )
    for key, market_name, market_type, industry_code, _ in markets:
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

        market_supply[key] = {
            "market_name": market_name,
            # Kiwoom signs cur_prc by direction; the displayed index is its magnitude.
            "market_index": abs(
                _required_market_number(response, "cur_prc", market_name)
            ),
            "market_change_rate": _required_market_number(
                response, "flu_rt", market_name
            ),
            "advancers": _required_market_count(response, "rising", market_name),
            "upper_limit_count": _required_market_count(
                response, "upl", market_name
            ),
            "decliners": _required_market_count(response, "fall", market_name),
            "lower_limit_count": _required_market_count(
                response, "lst", market_name
            ),
            "individual_eok": None,
            # Kiwoom REST does not expose these two HTS-wide common values.
            "foreign_futures_eok": None,
            "foreign_spot_eok": None,
            "institution_eok": None,
            "program_market_eok": None,
        }

    for base_date in _recent_dates(query_date):
        dated_flows = {}
        for key, market_name, market_type, _, program_market_type in markets:
            investor_flow = _fetch_market_investor_flow(
                access_token, market_name, market_type, base_date
            )
            program_flow = _fetch_market_program_flow(
                access_token, market_name, program_market_type, base_date
            )
            if investor_flow is None or program_flow is None:
                break
            dated_flows[key] = {
                **investor_flow,
                "program_market_eok": program_flow,
            }
        if len(dated_flows) == len(markets):
            for key, values in dated_flows.items():
                market_supply[key].update(values)
            return market_supply

    raise RuntimeError(
        f"ka10051/ka90005 market flow data unavailable through {query_date}"
    )


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
