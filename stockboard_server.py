import json
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kiwoom_data_provider import (
    KiwoomOpenApiRealtimeProvider, fetch_foreign_investor_net_after_close,
    fetch_foreign_sum, fetch_market_supply, fetch_ohlc, fetch_program_net,
    fetch_trade_value_top100, issue_access_token,
)
from stockboard_engine import (
    KST,
    _program_net_enabled,
    _query_date,
    _request_sleep_sec,
    prepare_display_rows,
)
from stockboard_store import RealtimeStore, _load_tradable_stock_codes


DOCS_DIR = Path(__file__).resolve().parent / "docs"
HOST = os.getenv("KIWOOM_HOST", "127.0.0.1")
PORT = int(os.getenv("KIWOOM_PORT", "8000"))


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


def _realtime_stock_codes(rows):
    codes = []
    seen_codes = set()
    for row in rows:
        stock_code = row.get("stock_code") if isinstance(row, dict) else None
        if (
            not isinstance(stock_code, str)
            or len(stock_code) != 6
            or not stock_code.isdigit()
            or stock_code in seen_codes
        ):
            continue
        seen_codes.add(stock_code)
        codes.append(f"{stock_code}_AL")
    return codes


def _apply_foreign_display(rows, market_session):
    use_foreign_investor_net = (
        market_session == "장마감"
        and any(
            isinstance(row.get("foreign_investor_net"), (int, float))
            and not isinstance(row.get("foreign_investor_net"), bool)
            for row in rows
        )
    )
    label = "외인(억)" if use_foreign_investor_net else "외합(억)"
    value_key = "foreign_investor_net" if use_foreign_investor_net else "foreign_sum"
    for row in rows:
        row["foreign_display_label"] = label
        row["foreign_display_value"] = row.get(value_key)
        row["foreign_display_source"] = value_key


def _realtime_number(value):
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


def _realtime_abs_number(value):
    number = _realtime_number(value)
    return abs(number) if number is not None else None


def _realtime_acc_trade_value_diagnostics(value):
    number = _realtime_number(value)
    if number is None:
        return {
            "raw": None,
            "million": None,
            "eok_candidate": None,
        }
    eok_candidate = number / 100
    return {
        "raw": number,
        "million": number,
        "eok_candidate": int(eok_candidate)
        if eok_candidate == int(eok_candidate)
        else eok_candidate,
    }


def _latest_event(events_by_code, stock_code):
    events = events_by_code.get(stock_code)
    if not events:
        return None
    event = events[-1]
    return event if isinstance(event, dict) else None


def _top100_with_realtime(rows, realtime_store):
    response_rows = [deepcopy(row) for row in rows]
    codes = [row.get("stock_code") for row in response_rows]
    try:
        snapshot = realtime_store.snapshot_many(codes)
    except Exception as error:
        print(
            f"warning: realtime overlay skipped for /api/top100: {error}",
            file=sys.stderr,
            flush=True,
        )
        snapshot = {"trade_events": {}, "orderbook_events": {}}

    trade_events = snapshot.get("trade_events", {})
    orderbook_events = snapshot.get("orderbook_events", {})
    for row in response_rows:
        stock_code = row.get("stock_code")
        row["rest_price"] = row.get("price")
        row["rest_change_rate"] = row.get("change_rate")

        trade_event = _latest_event(trade_events, stock_code)
        if trade_event is not None:
            realtime_price = _realtime_abs_number(trade_event.get("price"))
            realtime_change_rate = _realtime_number(
                trade_event.get("change_rate")
            )
            row["realtime_price"] = realtime_price
            row["realtime_change_rate"] = realtime_change_rate
            row["realtime_trade_time"] = trade_event.get("trade_time")
            row["realtime_strength"] = _realtime_number(
                trade_event.get("execution_strength")
            )
            row["realtime_acc_volume"] = _realtime_number(
                trade_event.get("cumulative_volume")
            )
            row["realtime_acc_trade_value"] = _realtime_number(
                trade_event.get("cumulative_value")
            )
            trade_value_diagnostics = _realtime_acc_trade_value_diagnostics(
                trade_event.get("cumulative_value")
            )
            row["realtime_acc_trade_value_raw"] = trade_value_diagnostics["raw"]
            row["realtime_acc_trade_value_million"] = trade_value_diagnostics[
                "million"
            ]
            row["realtime_acc_trade_value_eok_candidate"] = (
                trade_value_diagnostics["eok_candidate"]
            )
            row["realtime_received_at"] = trade_event.get("received_at")
            row["realtime_received_code"] = trade_event.get("received_code")
            row["realtime_registered_code"] = trade_event.get("registered_code")
            row["realtime_source_code"] = trade_event.get(
                "realtime_source_code"
            ) or trade_event.get("source_code")
            row["realtime_source"] = "tick"
            row["realtime_is_stale"] = False
            if realtime_price is not None:
                row["price"] = realtime_price
            if realtime_change_rate is not None:
                row["change_rate"] = realtime_change_rate

        orderbook_event = _latest_event(orderbook_events, stock_code)
        if orderbook_event is not None:
            row["bid_volume"] = _realtime_number(
                orderbook_event.get("total_bid_qty")
            )
            row["ask_volume"] = _realtime_number(
                orderbook_event.get("total_ask_qty")
            )
            row["best_bid_price"] = _realtime_abs_number(
                orderbook_event.get("best_bid")
            )
            row["best_ask_price"] = _realtime_abs_number(
                orderbook_event.get("best_ask")
            )
            row["orderbook_received_at"] = orderbook_event.get("received_at")
            row["orderbook_source"] = "orderbook"
    return response_rows


def _realtime_patch_payload(
    realtime_store,
    since_sequence=None,
    fallback=False,
    fallback_reason=None,
):
    mode = "full"
    if fallback or since_sequence is None:
        snapshot = realtime_store.snapshot_quotes_only()
    else:
        try:
            snapshot = realtime_store.snapshot_quotes_since(since_sequence)
            mode = "delta"
        except Exception as error:
            snapshot = realtime_store.snapshot_quotes_only()
            fallback = True
            fallback_reason = str(error) or "delta_failed"
    patches = []
    for stock_code, quote in snapshot.get("quotes", {}).items():
        price = _realtime_abs_number(quote.get("price"))
        change_rate = _realtime_number(quote.get("change_rate"))
        best_bid = _realtime_abs_number(quote.get("best_bid"))
        best_ask = _realtime_abs_number(quote.get("best_ask"))
        trade_value_diagnostics = _realtime_acc_trade_value_diagnostics(
            quote.get("cumulative_value")
        )
        patches.append(
            {
                "stock_code": stock_code,
                "price": price,
                "change_rate": change_rate,
                "realtime_price": price,
                "realtime_change_rate": change_rate,
                "trade_time": quote.get("trade_time"),
                "fid20_trade_time": quote.get("trade_time"),
                "received_code": quote.get("received_code"),
                "normalized_code": quote.get("normalized_code"),
                "registered_code": quote.get("registered_code"),
                "original_registered_code": quote.get(
                    "original_registered_code"
                ),
                "realtime_source_code": quote.get("realtime_source_code"),
                "source_code": quote.get("source_code"),
                "realtime_strength": _realtime_number(
                    quote.get("execution_strength")
                ),
                "realtime_acc_volume": _realtime_number(
                    quote.get("cumulative_volume")
                ),
                "realtime_acc_trade_value": _realtime_number(
                    quote.get("cumulative_value")
                ),
                "realtime_acc_trade_value_raw": trade_value_diagnostics["raw"],
                "realtime_acc_trade_value_million": trade_value_diagnostics[
                    "million"
                ],
                "realtime_acc_trade_value_eok_candidate": (
                    trade_value_diagnostics["eok_candidate"]
                ),
                "bid_volume": _realtime_number(quote.get("total_bid_qty")),
                "ask_volume": _realtime_number(quote.get("total_ask_qty")),
                "best_bid_price": best_bid,
                "best_ask_price": best_ask,
                "received_at": quote.get("received_at"),
                "sequence": quote.get("sequence"),
            }
        )
    return {
        "sequence": snapshot.get("sequence"),
        "updated_at": snapshot.get("updated_at"),
        "mode": mode,
        "since_sequence": since_sequence,
        "fallback": bool(fallback),
        "fallback_reason": fallback_reason,
        "rows": patches,
    }


def _empty_foreign_investor_net_data(query_date, error=None):
    return {
        "values": {},
        "stats": {
            "available": False,
            "query_date": query_date,
            "market_counts": {"KOSPI": 0, "KOSDAQ": 0},
            "market_stats": [],
            "collected_count": 0,
            "joined_count": 0,
            "errors": (
                [{"market": None, "error": str(error)}]
                if error is not None
                else []
            ),
            "rate_limit": None,
            "raw_samples": [],
            "converted_samples": [],
            "request_sleep_seconds": None,
        },
    }


def make_handler(
    rows,
    expected_row_count,
    expected_ohlc_count,
    expected_rows_id,
    market_supply,
    realtime_store,
    realtime_provider,
    realtime_provider_error,
    realtime_provider_start_requested,
    realtime_provider_start_succeeded,
    realtime_provider_register_requested,
    realtime_provider_register_succeeded,
    realtime_provider_registered_count,
    realtime_provider_register_error,
):
    class RequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

        def do_GET(self):
            parsed_url = urlparse(self.path)
            request_path = parsed_url.path
            query = parse_qs(parsed_url.query, keep_blank_values=True)
            if request_path == "/api/realtime":
                if "codes" in query:
                    codes = [
                        code.strip()
                        for value in query["codes"]
                        for code in value.split(",")
                        if code.strip()
                    ]
                    try:
                        response_payload = realtime_store.snapshot_many(codes)
                    except ValueError as error:
                        body = json.dumps({"error": str(error)}).encode("utf-8")
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(body)
                        return
                else:
                    response_payload = realtime_store.snapshot()
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime_status":
                snapshot = realtime_store.snapshot()
                response_payload = {
                    "sequence": snapshot["sequence"],
                    "updated_at": snapshot["updated_at"],
                    "quote_count": len(snapshot["quotes"]),
                    "trade_event_count": sum(
                        len(events) for events in snapshot["trade_events"].values()
                    ),
                    "orderbook_event_count": sum(
                        len(events)
                        for events in snapshot["orderbook_events"].values()
                    ),
                }
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime_patch":
                since_sequence = None
                fallback = False
                fallback_reason = None
                if "since_sequence" in query:
                    try:
                        since_sequence = int(query["since_sequence"][0])
                        if since_sequence < 0:
                            raise ValueError
                    except (TypeError, ValueError):
                        since_sequence = None
                        fallback = True
                        fallback_reason = "invalid_since_sequence"
                response_payload = _realtime_patch_payload(
                    realtime_store,
                    since_sequence=since_sequence,
                    fallback=fallback,
                    fallback_reason=fallback_reason,
                )
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if request_path == "/api/realtime_provider_status":
                if realtime_provider is None:
                    response_payload = {
                        "available": False,
                        "running": False,
                        "registered_count": 0,
                        "last_error": realtime_provider_error,
                        "last_received_at": None,
                    }
                else:
                    try:
                        response_payload = realtime_provider.status()
                    except Exception as error:
                        response_payload = {
                            "available": False,
                            "running": False,
                            "registered_count": 0,
                            "last_error": str(error),
                            "last_received_at": None,
                        }
                if realtime_provider_error and not response_payload.get("last_error"):
                    response_payload["last_error"] = realtime_provider_error
                response_payload["start_requested"] = (
                    realtime_provider_start_requested
                )
                response_payload["start_succeeded"] = (
                    realtime_provider_start_succeeded
                )
                response_payload["register_requested"] = (
                    realtime_provider_register_requested
                )
                response_payload["register_succeeded"] = (
                    realtime_provider_register_succeeded
                )
                if response_payload.get("registered_count") is None:
                    response_payload["registered_count"] = (
                        realtime_provider_registered_count
                    )
                response_payload["register_error"] = (
                    realtime_provider_register_error
                )
                body = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
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
                response_rows = _top100_with_realtime(rows, realtime_store)
                body = json.dumps(response_rows, ensure_ascii=False).encode("utf-8")
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
    program_net_enabled = _program_net_enabled()
    print(
        "ka90004 program net enabled: "
        f"{str(program_net_enabled).lower()}"
    )
    print(f"ka90004 query date: {query_date}")
    program_data = None
    program_net_by_code = {}
    if program_net_enabled:
        try:
            program_data = fetch_program_net(access_token, query_date)
            program_net_by_code = program_data["values"]
        except (RuntimeError, ValueError) as error:
            print(
                f"warning: ka90004 program net disabled after error: {error}",
                file=sys.stderr,
            )
    foreign_sum_data = None
    foreign_sum_by_code = {}
    try:
        foreign_sum_data = fetch_foreign_sum(access_token, query_date)
        foreign_sum_by_code = foreign_sum_data["values"]
    except (RuntimeError, ValueError) as error:
        print(
            f"warning: ka10037 foreign sum disabled after error: {error}",
            file=sys.stderr,
        )
    market_session = _market_session()
    foreign_investor_net_data = _empty_foreign_investor_net_data(query_date)
    if market_session == "장마감":
        try:
            foreign_investor_net_data = (
                fetch_foreign_investor_net_after_close(
                    access_token, query_date
                )
            )
        except (RuntimeError, ValueError) as error:
            foreign_investor_net_data = _empty_foreign_investor_net_data(
                query_date, error
            )
            print(
                f"warning: ka10066 foreign investor net disabled after error: "
                f"{error}",
                file=sys.stderr,
            )
    foreign_investor_net_by_code = foreign_investor_net_data["values"]
    filtered_rows = prepare_display_rows(
        top100_rows, tradable_codes, program_net_by_code
    )
    for row in filtered_rows:
        row["foreign_sum"] = foreign_sum_by_code.get(row["stock_code"])
        row["foreign_investor_net"] = foreign_investor_net_by_code.get(
            row["stock_code"]
        )
    _apply_foreign_display(filtered_rows, market_session)
    foreign_sum_joined_count = sum(
        row.get("foreign_sum") is not None for row in filtered_rows
    )
    if foreign_sum_data is not None:
        foreign_sum_data["stats"]["joined_count"] = foreign_sum_joined_count
    foreign_investor_net_joined_count = sum(
        row.get("foreign_investor_net") is not None for row in filtered_rows
    )
    foreign_investor_net_data["stats"]["joined_count"] = (
        foreign_investor_net_joined_count
    )
    program_net_joined_count = sum(
        row.get("program_net") is not None for row in filtered_rows
    )
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
    realtime_store = RealtimeStore()
    realtime_provider = None
    realtime_provider_error = None
    realtime_provider_start_requested = (
        os.getenv("STOCKBOARD_ENABLE_COM_REALTIME", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    realtime_provider_register_requested = (
        os.getenv("STOCKBOARD_REGISTER_TOP100_REALTIME", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    realtime_provider_start_succeeded = False
    realtime_provider_register_succeeded = False
    realtime_provider_registered_count = 0
    realtime_provider_register_error = None
    try:
        realtime_provider = KiwoomOpenApiRealtimeProvider(store=realtime_store)
    except Exception as error:
        realtime_provider_error = str(error)
        print(
            f"warning: realtime provider creation failed: {error}",
            file=sys.stderr,
        )
    if realtime_provider_start_requested and realtime_provider is not None:
        try:
            realtime_provider_start_succeeded = bool(realtime_provider.start())
            if not realtime_provider_start_succeeded:
                provider_status = realtime_provider.status()
                realtime_provider_error = (
                    provider_status.get("last_error")
                    or "realtime provider start returned false"
                )
        except Exception as error:
            realtime_provider_error = str(error)
            print(
                f"warning: realtime provider start failed: {error}",
                file=sys.stderr,
            )
    if realtime_provider_register_requested:
        if realtime_provider_start_succeeded and realtime_provider is not None:
            registration_codes = _realtime_stock_codes(filtered_rows)
            try:
                realtime_provider.register_codes(registration_codes)
                realtime_provider_register_succeeded = True
                provider_status = realtime_provider.status()
                realtime_provider_registered_count = provider_status.get(
                    "registered_count", len(registration_codes)
                )
            except Exception as error:
                realtime_provider_register_error = str(error)
                print(
                    f"warning: realtime provider registration failed: {error}",
                    file=sys.stderr,
                )
        else:
            realtime_provider_register_error = (
                "realtime provider start did not succeed"
            )
    server = ThreadingHTTPServer(
        (HOST, PORT),
        make_handler(
            filtered_rows,
            expected_row_count=len(filtered_rows),
            expected_ohlc_count=response_ohlc_count,
            expected_rows_id=id(filtered_rows),
            market_supply=market_supply,
            realtime_store=realtime_store,
            realtime_provider=realtime_provider,
            realtime_provider_error=realtime_provider_error,
            realtime_provider_start_requested=realtime_provider_start_requested,
            realtime_provider_start_succeeded=realtime_provider_start_succeeded,
            realtime_provider_register_requested=(
                realtime_provider_register_requested
            ),
            realtime_provider_register_succeeded=(
                realtime_provider_register_succeeded
            ),
            realtime_provider_registered_count=realtime_provider_registered_count,
            realtime_provider_register_error=realtime_provider_register_error,
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
    market_counts = (
        program_data["market_counts"]
        if program_data is not None
        else {"KOSPI": 0, "KOSDAQ": 0}
    )
    program_net_divisor = (
        program_data["divisor"]
        if program_data is not None
        else os.getenv("KIWOOM_PROGRAM_NET_EOK_DIVISOR", "100")
    )
    print(f"ka90004 market KOSPI count: {market_counts['KOSPI']}")
    print(f"ka90004 market KOSDAQ count: {market_counts['KOSDAQ']}")
    print(f"ka90004 collected count: {len(program_net_by_code)}")
    print(f"ka90004 joined count: {program_net_joined_count}")
    print(
        "ka90004 raw samples: "
        f"{program_data['raw_samples'] if program_data is not None else []}"
    )
    print(
        "ka90004 converted samples: "
        f"{program_data['converted_samples'] if program_data is not None else []}"
    )
    print(f"ka90004 divisor: {program_net_divisor}")
    print(
        "ka90004 request sleep sec: "
        f"{program_data['request_sleep_seconds'] if program_data is not None else None}"
    )
    print(
        "ka90004 errors: "
        f"{program_data['errors'] if program_data is not None else []}"
    )
    print(
        "ka90004 HTTP 429 position: "
        f"{program_data['rate_limit'] if program_data is not None else None}"
    )
    foreign_sum_stats = (
        foreign_sum_data["stats"]
        if foreign_sum_data is not None
        else {
            "enabled": os.getenv("KIWOOM_FOREIGN_SUM_ENABLED", "1").strip() == "1",
            "query_date": query_date,
            "market_counts": {"KOSPI": 0, "KOSDAQ": 0},
            "joined_count": foreign_sum_joined_count,
            "raw_samples": [],
            "converted_samples": [],
            "rate_limit": None,
        }
    )
    print(
        "ka10037 enabled: "
        f"{str(foreign_sum_stats['enabled']).lower()}"
    )
    print(f"ka10037 query date: {foreign_sum_stats['query_date']}")
    print(
        "ka10037 market KOSPI count: "
        f"{foreign_sum_stats['market_counts']['KOSPI']}"
    )
    print(
        "ka10037 market KOSDAQ count: "
        f"{foreign_sum_stats['market_counts']['KOSDAQ']}"
    )
    print(f"ka10037 joined count: {foreign_sum_stats['joined_count']}")
    print(f"ka10037 raw samples: {foreign_sum_stats['raw_samples']}")
    print(
        "ka10037 converted samples: "
        f"{foreign_sum_stats['converted_samples']}"
    )
    print(
        "ka10037 HTTP 429 position: "
        f"{foreign_sum_stats['rate_limit']}"
    )
    print(
        "foreign investor net after close available: "
        f"{str(foreign_investor_net_data['stats']['available']).lower()}"
    )
    print(
        "ka10066 market KOSPI count: "
        f"{foreign_investor_net_data['stats']['market_counts']['KOSPI']}"
    )
    print(
        "ka10066 market KOSDAQ count: "
        f"{foreign_investor_net_data['stats']['market_counts']['KOSDAQ']}"
    )
    print(
        "ka10066 collected count: "
        f"{foreign_investor_net_data['stats']['collected_count']}"
    )
    print(
        "foreign investor net after close joined count: "
        f"{foreign_investor_net_joined_count}"
    )
    print(
        "ka10066 raw samples: "
        f"{foreign_investor_net_data['stats']['raw_samples']}"
    )
    print(
        "ka10066 converted samples: "
        f"{foreign_investor_net_data['stats']['converted_samples']}"
    )
    print(
        "ka10066 request sleep sec: "
        f"{foreign_investor_net_data['stats']['request_sleep_seconds']}"
    )
    print(
        "ka10066 errors: "
        f"{foreign_investor_net_data['stats']['errors']}"
    )
    print(
        "ka10066 HTTP 429 position: "
        f"{foreign_investor_net_data['stats']['rate_limit']}"
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
        if realtime_provider_start_succeeded and realtime_provider is not None:
            try:
                realtime_provider.stop()
            except Exception as error:
                print(
                    f"warning: realtime provider stop failed: {error}",
                    file=sys.stderr,
                )
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
