import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
DOCS_DIR = Path(__file__).resolve().parent / "docs"
HOST = os.getenv("KIWOOM_HOST", "127.0.0.1")
PORT = int(os.getenv("KIWOOM_PORT", "8000"))
KST = timezone(timedelta(hours=9))
# ka90004 netprps_prica is provisionally treated as KRW millions: 100 million KRW per eok.
PROGRAM_NET_EOK_DIVISOR_TEXT = os.getenv("KIWOOM_PROGRAM_NET_EOK_DIVISOR", "100")
PROGRAM_NET_ENABLED_TEXT = os.getenv("KIWOOM_PROGRAM_NET_ENABLED", "0")
REQUEST_SLEEP_SEC_TEXT = os.getenv("KIWOOM_REQUEST_SLEEP_SEC", "0.25")

OUTPUT_KEYS = (
    "stock_code",
    "rank",
    "original_rank",
    "prev_rank",
    "grade",
    "stock_name",
    "price",
    "change_rate",
    "trade_value_eok",
    "ohlc",
    "bid_ask_ratio",
    "strength_1m",
    "strength_day",
    "foreign_sum",
    "program_net",
    "big_hand",
    "momentum",
)


class KiwoomAPIError(RuntimeError):
    def __init__(self, status_code, detail):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Kiwoom API HTTP {status_code}: {detail}")


def _listening_pids(host, port):
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"netstat failed while checking {host}:{port}: {result.stderr.strip()}"
        )

    endpoint = f"{host}:{port}".lower()
    pids = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 5 or fields[0].upper() != "TCP":
            continue
        if fields[1].lower() != endpoint or fields[3].upper() != "LISTENING":
            continue
        try:
            pid = int(fields[4])
            if pid > 0:
                pids.add(pid)
        except ValueError:
            continue
    return pids


def _stop_existing_server(host, port, timeout_seconds=5.0):
    current_pid = os.getpid()
    old_pids = sorted(_listening_pids(host, port) - {current_pid})
    print(f"current pid={current_pid}", flush=True)
    print(f"old server pids={old_pids}", flush=True)

    for pid in old_pids:
        print(f"old stockboard server found pid={pid}", flush=True)
        print("killing old stockboard server...", flush=True)
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if result.returncode != 0:
            print(
                f"warning: taskkill failed for pid={pid}: {result.stderr.strip()}",
                file=sys.stderr,
                flush=True,
            )

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining_pids = _listening_pids(host, port) - {current_pid}
        if not remaining_pids:
            if old_pids:
                print("old server killed.", flush=True)
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"port {host}:{port} is still listening on pids "
                f"{sorted(remaining_pids)}"
            )
        time.sleep(0.1)


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


def _first(row, *keys):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _clean_number(value):
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except InvalidOperation:
        return value
    return int(number) if number == number.to_integral() else float(number)


def _stock_code(value):
    if value in (None, ""):
        return None
    code = "".join(str(value).split()).upper()
    code = code.split("_", 1)[0]
    if code.startswith("A"):
        code = code[1:]
    if not code.isdigit():
        return None
    code = code.zfill(6)
    return code if len(code) == 6 else None


def _absolute_number(value):
    number = _clean_number(value)
    return abs(number) if isinstance(number, (int, float)) else number


def _trade_value_eok(value):
    """ka10032 거래대금(백만원)을 억원 단위로 변환한다."""
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    eok = Decimal(str(number)) / Decimal("100")
    return int(eok) if eok == eok.to_integral() else float(eok)


def _program_net_eok_divisor():
    try:
        divisor = Decimal(PROGRAM_NET_EOK_DIVISOR_TEXT)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_PROGRAM_NET_EOK_DIVISOR must be a positive number"
        ) from error
    if not divisor.is_finite() or divisor <= 0:
        raise RuntimeError("KIWOOM_PROGRAM_NET_EOK_DIVISOR must be a positive number")
    return divisor


def _program_net_eok(value, divisor):
    number = _clean_number(value)
    if not isinstance(number, (int, float)):
        return None
    eok = Decimal(str(number)) / divisor
    return int(eok) if eok == eok.to_integral() else float(eok)


def _query_date():
    query_date = os.getenv("KIWOOM_QUERY_DATE")
    if query_date:
        query_date = query_date.strip()
    else:
        query_date = datetime.now(KST).strftime("%Y%m%d")
    if len(query_date) != 8 or not query_date.isdigit():
        raise RuntimeError("KIWOOM_QUERY_DATE must use YYYYMMDD format")
    try:
        datetime.strptime(query_date, "%Y%m%d")
    except ValueError as error:
        raise RuntimeError("KIWOOM_QUERY_DATE must be a valid YYYYMMDD date") from error
    return query_date


def _program_net_enabled():
    return PROGRAM_NET_ENABLED_TEXT.strip() == "1"


def _request_sleep_sec():
    try:
        seconds = Decimal(REQUEST_SLEEP_SEC_TEXT)
    except InvalidOperation as error:
        raise RuntimeError(
            "KIWOOM_REQUEST_SLEEP_SEC must be a non-negative number"
        ) from error
    if not seconds.is_finite() or seconds < 0:
        raise RuntimeError("KIWOOM_REQUEST_SLEEP_SEC must be a non-negative number")
    return float(seconds)


def _normalize_row(row):
    normalized = dict.fromkeys(OUTPUT_KEYS)
    normalized.update(
        {
            "stock_code": _stock_code(_first(row, "stk_cd", "stock_code", "종목코드")),
            "rank": _clean_number(_first(row, "now_rank", "rank", "현재순위")),
            "prev_rank": _clean_number(_first(row, "pred_rank", "prev_rank", "전일순위")),
            "stock_name": _first(row, "stk_nm", "stock_name", "종목명"),
            "price": _absolute_number(_first(row, "cur_prc", "price", "현재가")),
            "change_rate": _clean_number(_first(row, "flu_rt", "change_rate", "등락률")),
            "trade_value_eok": _trade_value_eok(
                _first(row, "trde_prica", "trade_value", "거래대금")
            ),
        }
    )
    normalized["original_rank"] = normalized["rank"]
    return normalized


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
    raw_samples = []
    converted_samples = []

    for market_name, market_type in (("KOSPI", "P00101"), ("KOSDAQ", "P10102")):
        continuation = "N"
        next_key = ""
        seen_next_keys = set()
        page_count = 0
        row_count = 0

        while True:
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
            raw_rows = _first(
                response,
                "stk_prm_trde_prst",
                "stock_program_trade_status",
                "output",
            )
            if not isinstance(raw_rows, list):
                raise RuntimeError(f"Unexpected ka90004 response: {response}")

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
                    program_net_by_code[stock_code] = converted_value
                    if len(raw_samples) < 10:
                        raw_samples.append(
                            {"stock_code": stock_code, "netprps_prica": raw_value}
                        )
                        converted_samples.append(
                            {"stock_code": stock_code, "program_net": converted_value}
                        )

            continuation = response_headers.get("cont-yn", "").upper()
            next_key = response_headers.get("next-key", "")
            if continuation != "Y" or not next_key:
                break
            if next_key in seen_next_keys:
                raise RuntimeError(f"Repeated ka90004 next-key for {market_name}: {next_key}")
            seen_next_keys.add(next_key)

        market_stats.append(
            {"market": market_name, "pages": page_count, "rows": row_count}
        )

    return {
        "values": program_net_by_code,
        "market_stats": market_stats,
        "raw_samples": raw_samples,
        "converted_samples": converted_samples,
        "divisor": divisor,
    }


def _required_market_number(response, field, market_name):
    value = _clean_number(response.get(field))
    if not isinstance(value, (int, float)):
        raise RuntimeError(
            f"ka20001 {market_name} missing numeric field {field}: {response.get(field)!r}"
        )
    return value


def _required_market_count(response, field, market_name):
    value = _required_market_number(response, field, market_name)
    if value < 0 or int(value) != value:
        raise RuntimeError(
            f"ka20001 {market_name} invalid count field {field}: {value!r}"
        )
    return int(value)


def _market_flow_number(value, field, market_name):
    text = str(value).strip().replace(",", "")
    if text.startswith("--"):
        text = "-" + text[2:]
    number = _clean_number(text)
    if not isinstance(number, (int, float)):
        raise RuntimeError(
            f"{market_name} missing numeric market flow field {field}: {value!r}"
        )
    return number


def _million_to_eok(value, field, market_name):
    number = _market_flow_number(value, field, market_name)
    eok = Decimal(str(number)) / Decimal("100")
    return int(eok) if eok == eok.to_integral() else float(eok)


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


def _recent_dates(query_date, days=7):
    date = datetime.strptime(query_date, "%Y%m%d")
    return [
        (date - timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(days)
    ]


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


def _ohlc_price(value):
    number = _clean_number(value)
    return abs(number) if isinstance(number, (int, float)) else None


def _daily_row_date(row):
    value = _first(row, "date", "dt", "일자")
    if value in (None, ""):
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits if len(digits) == 8 else None


def _select_daily_rows(rows, query_date):
    eligible_rows = sorted(
        (
            (row_date, row)
            for row in rows
            if isinstance(row, dict)
            and (row_date := _daily_row_date(row))
            and row_date <= query_date
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not eligible_rows:
        return None, None

    current_date, current_row = eligible_rows[0]
    previous_row = next(
        (
            row
            for row_date, row in eligible_rows[1:]
            if row_date < current_date
        ),
        None,
    )
    return current_row, previous_row


def _build_ohlc(current_row, previous_row):
    if not current_row or not previous_row:
        return None, None

    prices = {
        "open": _ohlc_price(_first(current_row, "open_pric", "open")),
        "high": _ohlc_price(_first(current_row, "high_pric", "high")),
        "low": _ohlc_price(_first(current_row, "low_pric", "low")),
        "close": _ohlc_price(_first(current_row, "close_pric", "close")),
        "prev_high": _ohlc_price(_first(previous_row, "high_pric", "high")),
        "prev_close": _ohlc_price(_first(previous_row, "close_pric", "close")),
        "prev_low": _ohlc_price(_first(previous_row, "low_pric", "low")),
    }
    if any(value is None for value in prices.values()):
        return None, None
    if prices["high"] < prices["low"] or prices["prev_high"] < prices["prev_low"]:
        return None, None

    amount_million = _clean_number(_first(current_row, "amt_mn", "trade_value_mn"))
    trade_quantity = _clean_number(_first(current_row, "trde_qty", "trade_quantity"))
    vwap_candidate = None
    if (
        isinstance(amount_million, (int, float))
        and isinstance(trade_quantity, (int, float))
        and trade_quantity > 0
    ):
        vwap_decimal = (
            Decimal(str(amount_million)) * Decimal("1000000")
        ) / Decimal(str(trade_quantity))
        vwap_candidate = float(vwap_decimal)

    vwap = (
        round(vwap_candidate, 2)
        if vwap_candidate is not None
        and prices["low"] <= vwap_candidate <= prices["high"]
        else None
    )
    ohlc = {
        "open": prices["open"],
        "high": prices["high"],
        "low": prices["low"],
        "close": prices["close"],
        "vwap": vwap,
        "prev_high": prices["prev_high"],
        "prev_close": prices["prev_close"],
        "prev_low": prices["prev_low"],
        "trading_date": _daily_row_date(current_row),
    }
    return ohlc, {
        "amt_mn": amount_million,
        "trde_qty": trade_quantity,
        "calculated_vwap": round(vwap_candidate, 2)
        if vwap_candidate is not None
        else None,
        "accepted_vwap": vwap,
    }


def _ohlc_row_sample(row):
    if not row:
        return None
    return {
        "date": _first(row, "date", "dt", "일자"),
        "open_pric": _first(row, "open_pric", "open"),
        "high_pric": _first(row, "high_pric", "high"),
        "low_pric": _first(row, "low_pric", "low"),
        "close_pric": _first(row, "close_pric", "close"),
        "amt_mn": _first(row, "amt_mn", "trade_value_mn"),
        "trde_qty": _first(row, "trde_qty", "trade_quantity"),
    }


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


def _load_tradable_stock_codes():
    master_path = DOCS_DIR / "tradable_stock_master.csv"
    with master_path.open("r", encoding="utf-8-sig", newline="") as master_file:
        reader = csv.reader(master_file)
        next(reader, None)
        return {
            code
            for row in reader
            if row and (code := _stock_code(row[0]))
        }


def _market_session(now=None):
    current = now or datetime.now(KST)
    if current.weekday() >= 5:
        return "장마감"
    minutes = current.hour * 60 + current.minute
    if 8 * 60 <= minutes < 9 * 60:
        return "프리마켓"
    if 9 * 60 <= minutes < 15 * 60 + 30:
        return "정규장"
    if 15 * 60 + 30 <= minutes < 20 * 60:
        return "애프터마켓"
    return "장마감"


def make_handler(
    rows,
    expected_row_count,
    expected_ohlc_count,
    expected_rows_id,
    market_supply,
):
    class RequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

        def do_GET(self):
            request_path = self.path.split("?", 1)[0]
            if request_path == "/api/market_supply":
                response_payload = {
                    "market_session": _market_session(),
                    "kospi": market_supply["kospi"],
                    "kosdaq": market_supply["kosdaq"],
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/top100":
                ohlc_count = sum(row.get("ohlc") is not None for row in rows)
                rows_id = id(rows)
                print(
                    f"/api/top100 response rows: {len(rows)}, "
                    f"ohlc count: {ohlc_count}, rows_id: {rows_id}, "
                    f"pid: {os.getpid()}",
                    flush=True,
                )
                if (
                    len(rows) != expected_row_count
                    or ohlc_count != expected_ohlc_count
                    or rows_id != expected_rows_id
                ):
                    detail = {
                        "error": "top100 response invariant failed",
                        "rows": len(rows),
                        "ohlc": ohlc_count,
                    }
                    body = json.dumps(detail).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-StockBoard-PID", str(os.getpid()))
                self.send_header("X-StockBoard-OHLC-Count", str(ohlc_count))
                self.send_header("X-StockBoard-Rows-ID", str(rows_id))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    return RequestHandler


def main():
    _stop_existing_server(HOST, PORT)

    if not DOCS_DIR.is_dir():
        raise RuntimeError(f"docs directory not found: {DOCS_DIR}")

    access_token = issue_access_token()
    query_date = _query_date()
    market_supply = fetch_market_supply(access_token, query_date)
    top100_rows, page_counts = fetch_trade_value_top100(access_token)
    tradable_codes = _load_tradable_stock_codes()
    print(f"master count: {len(tradable_codes)}")
    print(f"master code samples: {sorted(tradable_codes)[:10]}")
    print(f"API normalized code samples: {[row['stock_code'] for row in top100_rows[:10]]}")
    filtered_rows = [row for row in top100_rows if row["stock_code"] in tradable_codes]
    program_data = None
    program_net_by_code = {}
    if _program_net_enabled():
        try:
            program_data = fetch_program_net(access_token, query_date)
            program_net_by_code = program_data["values"]
        except (RuntimeError, ValueError) as error:
            print(
                f"warning: ka90004 program net disabled after error: {error}",
                file=sys.stderr,
            )
    for display_rank, row in enumerate(filtered_rows, start=1):
        row["rank"] = display_rank
        row["program_net"] = program_net_by_code.get(row["stock_code"])
    request_sleep_sec = _request_sleep_sec()
    ohlc_data = fetch_ohlc(
        access_token,
        filtered_rows,
        query_date,
        request_sleep_sec,
    )
    top100_count = len(filtered_rows)
    response_ohlc_count = sum(row.get("ohlc") is not None for row in filtered_rows)
    if (
        id(filtered_rows) != ohlc_data["rows_id"]
        or response_ohlc_count != ohlc_data["joined_count"]
        or response_ohlc_count != ohlc_data["attached_count"]
    ):
        raise RuntimeError(
            "ka10086 fetch_ohlc result does not match /api/top100 rows: "
            f"joined={ohlc_data['joined_count']}, "
            f"attached={ohlc_data['attached_count']}, "
            f"response={response_ohlc_count}, "
            f"fetch_rows_id={ohlc_data['rows_id']}, "
            f"response_rows_id={id(filtered_rows)}"
        )
    print(
        f"ka10086 OHLC count before /api/top100: {response_ohlc_count}, "
        f"rows: {len(filtered_rows)}, rows_id: {id(filtered_rows)}, "
        f"pid: {os.getpid()}",
        flush=True,
    )
    server = ThreadingHTTPServer(
        (HOST, PORT),
        make_handler(
            filtered_rows,
            expected_row_count=len(filtered_rows),
            expected_ohlc_count=response_ohlc_count,
            expected_rows_id=id(filtered_rows),
            market_supply=market_supply,
        ),
    )
    print(f"server pid={os.getpid()}", flush=True)
    print(f"server url=http://{HOST}:{PORT}/", flush=True)
    for page_number, page_count in enumerate(page_counts, start=1):
        print(f"page{page_number} count: {page_count}")
    print(f"unique count: {len(top100_rows)}")
    print(f"filtered count: {len(filtered_rows)}")
    print(f"top100 count: {top100_count}")
    print(f"ka20001 market supply: {market_supply}")
    if program_data is None:
        print("ka90004 program net: disabled or unavailable")
    else:
        print(f"ka90004 query date: {query_date}")
        print(f"ka90004 eok divisor: {program_data['divisor']}")
        for market_stat in program_data["market_stats"]:
            print(
                f"ka90004 {market_stat['market']} pages: {market_stat['pages']}, "
                f"rows: {market_stat['rows']}"
            )
        print(f"ka90004 raw samples: {program_data['raw_samples']}")
        print(f"ka90004 converted samples: {program_data['converted_samples']}")
        print(
            "ka90004 matched count: "
            f"{sum(row['program_net'] is not None for row in filtered_rows)}"
        )
    print(f"ka10086 query date: {query_date}")
    print("ka10086 OHLC limit: all")
    print(f"ka10086 request sleep sec: {request_sleep_sec}")
    print(f"ka10086 target count: {ohlc_data['target_count']}")
    print(f"ka10086 OHLC joined count: {ohlc_data['joined_count']}")
    print(f"ka10086 OHLC failed count: {ohlc_data['failed_count']}")
    print(f"ka10086 OHLC first failed samples: {ohlc_data['failed_samples']}")
    print(f"ka10086 raw samples: {ohlc_data['raw_samples']}")
    print(f"ka10086 converted samples: {ohlc_data['converted_samples']}")
    print(f"ka10086 VWAP samples: {ohlc_data['vwap_samples']}")
    print(f"ka10086 HTTP 429 position: {ohlc_data['rate_limit']}")
    print(f"Open http://{HOST}:{PORT}/stockboard_v0_3_0_sample.html")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
