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


def _post_json(path, payload, headers=None):
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
            return json.loads(response.read().decode("utf-8"))
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
    code = str(value).strip().upper()
    if code.startswith("A") and len(code) == 7:
        code = code[1:]
    return code.zfill(6) if code.isdigit() else code


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
    """거래대금상위요청(ka10032)을 한 번 호출해 최대 100건을 반환한다."""
    response = _post_json(
        "/api/dostk/rkinfo",
        {
            "mrkt_tp": "000",
            "mang_stk_incls": "0",
            "stex_tp": "3",
        },
        {
            "Authorization": f"Bearer {access_token}",
            "api-id": "ka10032",
            "cont-yn": "N",
            "next-key": "",
        },
    )
    raw_rows = _first(
        response,
        "trde_prica_upper",
        "trade_value_top",
        "output",
    )
    if not isinstance(raw_rows, list):
        raise RuntimeError(f"Unexpected ka10032 response: {response}")
    return [_normalize_row(row) for row in raw_rows[:100] if isinstance(row, dict)]


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
    top100_rows = fetch_trade_value_top100(access_token)
    top100_cache = {"rows": top100_rows}
    server = ThreadingHTTPServer((HOST, PORT), make_handler(top100_cache))
    print(f"Fetched {len(top100_rows)} ka10032 rows")
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
