import csv
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_BASE_URL = os.getenv("KIWOOM_API_BASE_URL", "https://api.kiwoom.com").rstrip("/")
DOCS_DIR = Path(__file__).resolve().parent / "docs"
HOST = os.getenv("KIWOOM_HOST", "127.0.0.1")
PORT = int(os.getenv("KIWOOM_PORT", "8000"))

OUTPUT_KEYS = (
    "stock_code",
    "rank",
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
        raise RuntimeError(f"Kiwoom API HTTP {error.code}: {detail}") from error
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


def make_handler(top100_cache):
    class RequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

        def do_GET(self):
            if self.path.split("?", 1)[0] == "/api/top100":
                body = json.dumps(top100_cache["rows"], ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    return RequestHandler


def main():
    if not DOCS_DIR.is_dir():
        raise RuntimeError(f"docs directory not found: {DOCS_DIR}")

    access_token = issue_access_token()
    top100_rows, page_counts = fetch_trade_value_top100(access_token)
    tradable_codes = _load_tradable_stock_codes()
    print(f"master count: {len(tradable_codes)}")
    print(f"master code samples: {sorted(tradable_codes)[:10]}")
    print(f"API normalized code samples: {[row['stock_code'] for row in top100_rows[:10]]}")
    filtered_rows = [row for row in top100_rows if row["stock_code"] in tradable_codes]
    top100_count = min(len(filtered_rows), 100)
    top100_cache = {"rows": top100_rows}
    server = ThreadingHTTPServer((HOST, PORT), make_handler(top100_cache))
    for page_number, page_count in enumerate(page_counts, start=1):
        print(f"page{page_number} count: {page_count}")
    print(f"unique count: {len(top100_rows)}")
    print(f"filtered count: {len(filtered_rows)}")
    print(f"top100 count: {top100_count}")
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
