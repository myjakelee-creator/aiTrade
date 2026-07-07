from __future__ import annotations

import argparse
import json
import socketserver
import sys
import threading
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import (  # noqa: E402
    DEFAULT_EVENT_PORT,
    DEFAULT_HOST,
    DEFAULT_WEB_PORT,
    LARGE_TRADE_THRESHOLD_KRW,
    RUNTIME_DIR,
    append_jsonl,
    atomic_write_json,
    event_age_sec,
    normalize_code,
    normalize_trade_time,
    normalized_price,
    normalized_rate,
    normalized_trade_value_eok,
    now_text,
    to_int,
    to_number,
    trading_date_text,
)


def merged_event_values(event: dict[str, Any]) -> dict[str, Any]:
    """Merge collector event payloads.

    The v2 collector uses the existing KiwoomOpenApiRealtimeProvider adapter.
    That provider calls store.update_trade(code, **fields), so the useful fields
    arrive in event["kwargs"], while raw/direct adapters may use event["values"].
    """
    result: dict[str, Any] = {}
    values = event.get("values")
    if isinstance(values, dict):
        result.update(values)
    kwargs = event.get("kwargs")
    if isinstance(kwargs, dict):
        result.update(kwargs)
    return result


class State:
    def __init__(self, universe_file: Path):
        self.lock = threading.RLock()
        self.universe_file = universe_file
        self.name_by_code: dict[str, str] = {}
        self.seed_rank_by_code: dict[str, int] = {}
        self.quotes: dict[str, dict[str, Any]] = {}
        self.status: dict[str, Any] = {
            "started_at": now_text(),
            "event_count": 0,
            "trade_count": 0,
            "orderbook_count": 0,
            "collector_status": None,
            "last_event_at": None,
            "last_error": None,
            "tcp_clients": 0,
        }
        self._load_universe()

    def _load_universe(self) -> None:
        try:
            payload = json.loads(self.universe_file.read_text(encoding="utf-8-sig"))
            for item in payload.get("items", []) or []:
                code = normalize_code(item.get("stock_code"))
                if not code:
                    continue
                self.name_by_code[code] = str(item.get("stock_name") or code)
                self.seed_rank_by_code[code] = int(
                    item.get("seed_rank") or item.get("original_rank") or 999999
                )
        except Exception as error:
            self.status["last_error"] = f"universe load failed: {error}"

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        with self.lock:
            self.status["event_count"] += 1
            self.status["last_event_at"] = event.get("ts") or now_text()
            if event_type in {"collector_status", "collector_heartbeat", "register_result"}:
                self.status["collector_status"] = deepcopy(event)
                return
            if event_type == "trade":
                self._apply_trade(event)
            elif event_type == "orderbook":
                self._apply_orderbook(event)
            elif event_type == "close_metrics":
                self._apply_close_metrics(event)

    def _quote(self, code: str) -> dict[str, Any]:
        quote = self.quotes.get(code)
        if quote is None:
            quote = {
                "stock_code": code,
                "stock_name": self.name_by_code.get(code, code),
                "seed_rank": self.seed_rank_by_code.get(code, 999999),
                "large_trade_buy_count": 0,
                "large_trade_sell_count": 0,
                "large_trade_net_count": 0,
                "large_trade_buy_sum_eok": 0.0,
                "large_trade_sell_sum_eok": 0.0,
                "large_trade_net_sum_eok": 0.0,
            }
            self.quotes[code] = quote
        return quote

    def _apply_trade(self, event: dict[str, Any]) -> None:
        values = merged_event_values(event)
        raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return
        quote = self._quote(code)
        price = normalized_price(
            raw.get("price_raw")
            or values.get("price")
            or values.get("trade_price")
            or values.get("realtime_price")
        )
        change_rate = normalized_rate(
            raw.get("change_rate_raw")
            or values.get("change_rate")
            or values.get("realtime_change_rate")
        )
        trade_qty = to_int(raw.get("trade_qty_raw") or values.get("trade_qty"))
        cumulative_volume = to_int(
            raw.get("cumulative_volume_raw") or values.get("cumulative_volume")
        )
        trade_value_eok = None
        if values.get("trade_value_eok") not in (None, ""):
            trade_value_eok = to_number(values.get("trade_value_eok"))
        if trade_value_eok is None:
            trade_value_eok = normalized_trade_value_eok(
                raw.get("cumulative_value_raw") or values.get("cumulative_value")
            )
        strength = to_number(
            raw.get("execution_strength_raw") or values.get("execution_strength")
        )
        trade_time = normalize_trade_time(
            raw.get("trade_time_raw") or values.get("trade_time") or values.get("fid20_trade_time")
        )
        received_at = (
            event.get("ts")
            or values.get("price_received_at")
            or values.get("trade_received_at")
            or values.get("received_at")
            or now_text()
        )
        if price is not None:
            quote["price"] = price
            quote["trade_price"] = price
        if change_rate is not None:
            quote["change_rate"] = change_rate
        if trade_qty is not None:
            quote["trade_qty"] = trade_qty
        if cumulative_volume is not None:
            quote["cumulative_volume"] = cumulative_volume
        if trade_value_eok is not None:
            quote["trade_value_eok"] = round(float(trade_value_eok), 4)
        if strength is not None:
            quote["execution_strength"] = round(strength, 4)
        quote["trade_time"] = trade_time
        quote["received_at"] = received_at
        quote["price_age_sec"] = event_age_sec(received_at)
        quote["market_type_raw"] = raw.get("market_type_raw") or values.get("market_type")
        quote["source_code"] = values.get("source_code") or values.get("registered_code")
        self.status["trade_count"] += 1
        if trade_qty and price:
            trade_amount = abs(trade_qty) * price
            if trade_amount >= LARGE_TRADE_THRESHOLD_KRW:
                eok = trade_amount / 100_000_000
                if trade_qty > 0:
                    quote["large_trade_buy_count"] += 1
                    quote["large_trade_buy_sum_eok"] = round(
                        quote["large_trade_buy_sum_eok"] + eok, 4
                    )
                elif trade_qty < 0:
                    quote["large_trade_sell_count"] += 1
                    quote["large_trade_sell_sum_eok"] = round(
                        quote["large_trade_sell_sum_eok"] + eok, 4
                    )
                quote["large_trade_net_count"] = (
                    quote["large_trade_buy_count"] - quote["large_trade_sell_count"]
                )
                quote["large_trade_net_sum_eok"] = round(
                    quote["large_trade_buy_sum_eok"] - quote["large_trade_sell_sum_eok"],
                    4,
                )

    def _apply_orderbook(self, event: dict[str, Any]) -> None:
        values = merged_event_values(event)
        raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return
        quote = self._quote(code)
        ask_volume = to_int(raw.get("ask_volume_raw") or values.get("ask_volume"))
        bid_volume = to_int(raw.get("bid_volume_raw") or values.get("bid_volume"))
        best_ask = normalized_price(raw.get("best_ask_price_raw") or values.get("best_ask"))
        best_bid = normalized_price(raw.get("best_bid_price_raw") or values.get("best_bid"))
        if ask_volume is not None:
            quote["ask_volume"] = ask_volume
        if bid_volume is not None:
            quote["bid_volume"] = bid_volume
        if best_ask is not None:
            quote["best_ask_price"] = best_ask
        if best_bid is not None:
            quote["best_bid_price"] = best_bid
        if ask_volume is not None and bid_volume is not None and (ask_volume + bid_volume) > 0:
            quote["bid_pct"] = round(bid_volume / (ask_volume + bid_volume) * 100)
            quote["ask_pct"] = 100 - quote["bid_pct"]
            quote["bid_ask_ratio"] = round(bid_volume / ask_volume, 4) if ask_volume > 0 else None
        quote["orderbook_received_at"] = event.get("ts") or now_text()
        self.status["orderbook_count"] += 1

    def _apply_close_metrics(self, event: dict[str, Any]) -> None:
        values = merged_event_values(event)
        code = normalize_code(event.get("stock_code") or values.get("stock_code"))
        if not code:
            return
        self._quote(code).update(values)

    def rows(self, limit: int = 300) -> list[dict[str, Any]]:
        with self.lock:
            rows = [deepcopy(row) for row in self.quotes.values()]
        for row in rows:
            row["price_age_sec"] = event_age_sec(row.get("received_at"))
        rows.sort(
            key=lambda row: (
                -(to_number(row.get("trade_value_eok")) or 0),
                row.get("seed_rank") or 999999,
                row.get("stock_code") or "",
            )
        )
        for rank, row in enumerate(rows, start=1):
            row["rank"] = rank
        return rows[:limit]

    def snapshot(self, limit: int = 300) -> dict[str, Any]:
        with self.lock:
            status = deepcopy(self.status)
        rows = self.rows(limit)
        return {
            "schema_version": 1,
            "source": "stockboard_v2_worker64",
            "ts": now_text(),
            "trading_date": trading_date_text(),
            "status": status,
            "row_count": len(rows),
            "rows": rows,
        }


class EventTCPHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.server.state.status["tcp_clients"] += 1
        try:
            for raw_line in self.rfile:
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                    if isinstance(event, dict):
                        self.server.state.apply_event(event)
                        self.server.record_event(event)
                except Exception as error:
                    self.server.state.status["last_error"] = f"event parse failed: {error}"
        finally:
            self.server.state.status["tcp_clients"] = max(
                0, self.server.state.status.get("tcp_clients", 1) - 1
            )


class EventTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self, server_address, handler_class, state: State, event_log: Path):
        super().__init__(server_address, handler_class)
        self.state = state
        self.event_log = event_log

    def record_event(self, event: dict[str, Any]) -> None:
        append_jsonl(self.event_log, event)


class WebHandler(BaseHTTPRequestHandler):
    server_version = "StockBoardV2/0.1"

    def _json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path in {"/", "/v2", "/stockboard_v2.html"}:
            html_path = ROOT / "docs" / "stockboard_v2.html"
            body = html_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/v2/health":
            self._json({"ok": True, "ts": now_text(), "pid": getattr(self.server, "pid", None)})
            return
        if parsed.path == "/api/v2/snapshot":
            try:
                limit = int(query.get("limit", ["300"])[0])
            except (TypeError, ValueError):
                limit = 300
            self._json(self.server.state.snapshot(limit=max(1, min(1000, limit))))
            return
        self._json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)


class WebServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, state: State):
        super().__init__(server_address, handler_class)
        self.state = state
        self.pid = __import__("os").getpid()


def write_status_loop(state: State, output_path: Path, stop_event: threading.Event) -> None:
    while not stop_event.wait(1.0):
        try:
            atomic_write_json(output_path, state.snapshot(limit=300))
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard v2 64-bit realtime worker/web")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--event-port", type=int, default=DEFAULT_EVENT_PORT)
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--universe", default=str(RUNTIME_DIR / "universe.json"))
    args = parser.parse_args()

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    state = State(Path(args.universe))
    event_log = RUNTIME_DIR / f"events_{trading_date_text()}.jsonl"
    snapshot_file = RUNTIME_DIR / "snapshot.json"
    stop_event = threading.Event()
    tcp_server = EventTCPServer((args.host, args.event_port), EventTCPHandler, state, event_log)
    web_server = WebServer((args.host, args.web_port), WebHandler, state)
    threading.Thread(target=tcp_server.serve_forever, name="stockboard-v2-event-tcp", daemon=True).start()
    threading.Thread(
        target=write_status_loop,
        args=(state, snapshot_file, stop_event),
        name="stockboard-v2-snapshot-writer",
        daemon=True,
    ).start()
    print(
        f"StockBoard v2 worker listening event={args.host}:{args.event_port} web=http://{args.host}:{args.web_port}/",
        flush=True,
    )
    print(f"EVENT_LOG={event_log}", flush=True)
    print(f"SNAPSHOT_FILE={snapshot_file}", flush=True)
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        tcp_server.shutdown()
        web_server.server_close()
        tcp_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
