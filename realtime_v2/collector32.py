from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kiwoom_data_provider import KiwoomOpenApiRealtimeProvider  # noqa: E402
from realtime_v2.common import DEFAULT_EVENT_PORT, DEFAULT_HOST, normalize_code, now_text, safe_json_dumps  # noqa: E402


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _event_trade_qty(event: dict[str, Any]) -> int | None:
    values = event.get("values") if isinstance(event.get("values"), dict) else {}
    kwargs = event.get("kwargs") if isinstance(event.get("kwargs"), dict) else {}
    raw = kwargs.get("raw") if isinstance(kwargs.get("raw"), dict) else values.get("raw") if isinstance(values.get("raw"), dict) else {}
    return _to_int(
        raw.get("trade_qty_raw")
        or kwargs.get("trade_qty")
        or values.get("trade_qty")
        or kwargs.get("cntg_vol")
        or values.get("cntg_vol")
    )


class EventSender(threading.Thread):
    """Send display events to the 64-bit worker without replaying every tick.

    Display quotes are coalesced to the latest event per symbol, but 1-minute
    strength needs every signed trade quantity.  To keep both speed and accuracy,
    the collector aggregates signed buy/sell quantity per symbol during each
    micro-batch and attaches that flow to the latest display event.
    """

    def __init__(self, host: str, port: int, flush_ms: int = 50):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.flush_sec = max(0.01, min(1.0, float(flush_ms) / 1000.0))
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_trade_by_code: dict[str, dict[str, Any]] = {}
        self.latest_orderbook_by_code: dict[str, dict[str, Any]] = {}
        self.trade_flow_by_code: dict[str, dict[str, int]] = {}
        self.direct_events: deque[dict[str, Any]] = deque()
        self.sent_count = 0
        self.sent_per_sec = 0.0
        self.received_trade_count = 0
        self.received_orderbook_count = 0
        self.received_direct_count = 0
        self.coalesced_trade_overwrite_count = 0
        self.coalesced_orderbook_overwrite_count = 0
        self.flow_trade_count = 0
        self.flow_buy_qty = 0
        self.flow_sell_qty = 0
        self.last_flush_count = 0
        self.last_flush_at = None
        self.last_error = None
        self.connected = False
        self._rate_at = time.monotonic()
        self._rate_sent_count = 0

    def publish_trade(self, event: dict[str, Any]) -> None:
        code = normalize_code(event.get("stock_code"))
        qty = _event_trade_qty(event)
        with self.lock:
            self.received_trade_count += 1
            if code:
                if qty:
                    flow = self.trade_flow_by_code.setdefault(code, {"buy_qty": 0, "sell_qty": 0, "trade_count": 0})
                    if qty > 0:
                        flow["buy_qty"] += int(qty)
                        self.flow_buy_qty += int(qty)
                    elif qty < 0:
                        flow["sell_qty"] += abs(int(qty))
                        self.flow_sell_qty += abs(int(qty))
                    flow["trade_count"] += 1
                    self.flow_trade_count += 1
                if code in self.latest_trade_by_code:
                    self.coalesced_trade_overwrite_count += 1
                self.latest_trade_by_code[code] = event
            else:
                self.direct_events.append(event)

    def publish_orderbook(self, event: dict[str, Any]) -> None:
        code = normalize_code(event.get("stock_code"))
        with self.lock:
            self.received_orderbook_count += 1
            if code:
                if code in self.latest_orderbook_by_code:
                    self.coalesced_orderbook_overwrite_count += 1
                self.latest_orderbook_by_code[code] = event
            else:
                self.direct_events.append(event)

    def publish_direct(self, event: dict[str, Any]) -> None:
        with self.lock:
            self.received_direct_count += 1
            self.direct_events.append(event)

    def _attach_trade_flow(self, code: str, event: dict[str, Any], flow: dict[str, int]) -> dict[str, Any]:
        if not flow:
            return event
        next_event = dict(event)
        kwargs = dict(next_event.get("kwargs") if isinstance(next_event.get("kwargs"), dict) else {})
        kwargs["collector_buy_qty"] = int(flow.get("buy_qty") or 0)
        kwargs["collector_sell_qty"] = int(flow.get("sell_qty") or 0)
        kwargs["collector_trade_count"] = int(flow.get("trade_count") or 0)
        kwargs["collector_flow_window_ms"] = int(self.flush_sec * 1000)
        next_event["kwargs"] = kwargs
        return next_event

    def _drain(self) -> list[dict[str, Any]]:
        with self.lock:
            direct = list(self.direct_events)
            trade_items = list(self.latest_trade_by_code.items())
            orderbooks = list(self.latest_orderbook_by_code.values())
            flows = dict(self.trade_flow_by_code)
            self.direct_events.clear()
            self.latest_trade_by_code.clear()
            self.latest_orderbook_by_code.clear()
            self.trade_flow_by_code.clear()
        trades = [self._attach_trade_flow(code, event, flows.get(code, {})) for code, event in trade_items]
        return [*direct, *trades, *orderbooks]

    def _requeue_unsent(self, events: list[dict[str, Any]]) -> None:
        with self.lock:
            for event in events:
                event_type = event.get("type")
                code = normalize_code(event.get("stock_code"))
                if event_type == "trade" and code:
                    self.latest_trade_by_code[code] = event
                elif event_type == "orderbook" and code:
                    self.latest_orderbook_by_code[code] = event
                else:
                    self.direct_events.appendleft(event)

    def _update_rate(self) -> None:
        now = time.monotonic()
        elapsed = now - self._rate_at
        if elapsed < 1.0:
            return
        sent_delta = self.sent_count - self._rate_sent_count
        self.sent_per_sec = round(sent_delta / elapsed, 2) if elapsed > 0 else 0.0
        self._rate_at = now
        self._rate_sent_count = self.sent_count

    def _send_batch(self, sock, events: list[dict[str, Any]]) -> bool:
        for index, event in enumerate(events):
            try:
                sock.sendall(safe_json_dumps(event).encode("utf-8") + b"\n")
                self.sent_count += 1
            except OSError as error:
                self.last_error = str(error)
                self._requeue_unsent(events[index:])
                return False
        if events:
            self.last_flush_count = len(events)
            self.last_flush_at = now_text()
        self._update_rate()
        return True

    def run(self) -> None:
        sock = None
        while not self.stop_event.is_set():
            if sock is None:
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=1.0)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.connected = True
                    self.last_error = None
                except OSError as error:
                    self.connected = False
                    self.last_error = str(error)
                    time.sleep(0.2)
                    continue
            time.sleep(self.flush_sec)
            events = self._drain()
            if not events:
                self._update_rate()
                continue
            if not self._send_batch(sock, events):
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                self.connected = False
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def stats(self) -> dict[str, Any]:
        with self.lock:
            pending_trade = len(self.latest_trade_by_code)
            pending_orderbook = len(self.latest_orderbook_by_code)
            pending_direct = len(self.direct_events)
            pending_flow = len(self.trade_flow_by_code)
        return {
            "connected": self.connected,
            "flush_ms": int(self.flush_sec * 1000),
            "pending_trade_count": pending_trade,
            "pending_orderbook_count": pending_orderbook,
            "pending_direct_count": pending_direct,
            "pending_flow_code_count": pending_flow,
            "pending_total_count": pending_trade + pending_orderbook + pending_direct,
            "received_trade_count": self.received_trade_count,
            "received_orderbook_count": self.received_orderbook_count,
            "received_direct_count": self.received_direct_count,
            "coalesced_trade_overwrite_count": self.coalesced_trade_overwrite_count,
            "coalesced_orderbook_overwrite_count": self.coalesced_orderbook_overwrite_count,
            "flow_trade_count": self.flow_trade_count,
            "flow_buy_qty": self.flow_buy_qty,
            "flow_sell_qty": self.flow_sell_qty,
            "sent_count": self.sent_count,
            "sent_per_sec": self.sent_per_sec,
            "last_flush_count": self.last_flush_count,
            "last_flush_at": self.last_flush_at,
            "last_error": self.last_error,
        }

    def stop(self) -> None:
        self.stop_event.set()


class PublishingStore:
    def __init__(self, sender: EventSender):
        self.sender = sender

    def update_trade(self, stock_code, values=None, **kwargs):
        self.sender.publish_trade(
            {
                "type": "trade",
                "ts": now_text(),
                "stock_code": normalize_code(stock_code),
                "values": values if isinstance(values, dict) else {},
                "kwargs": kwargs,
            }
        )
        return values if isinstance(values, dict) else {}

    def update_orderbook(self, stock_code, orderbook=None, **kwargs):
        self.sender.publish_orderbook(
            {
                "type": "orderbook",
                "ts": now_text(),
                "stock_code": normalize_code(stock_code),
                "values": orderbook if isinstance(orderbook, dict) else {},
                "kwargs": kwargs,
            }
        )
        return orderbook if isinstance(orderbook, dict) else {}

    def update_close_metrics(self, stock_code, metrics):
        self.sender.publish_direct(
            {
                "type": "close_metrics",
                "ts": now_text(),
                "stock_code": normalize_code(stock_code),
                "values": metrics if isinstance(metrics, dict) else {},
            }
        )
        return metrics

    def set_base_ohlc_many(self, rows):
        return None

    def one_min_bucket_diagnostics(self):
        return {}

    def latest_only_diagnostics(self):
        return {}

    def snapshot_latest_many(self, codes):
        return {"quotes": {}, "close_metrics": {}}

    def close_metrics_snapshot(self, codes=None):
        return {}


def load_codes(path_text: str, codes_text: str, limit: int, suffix: str) -> list[str]:
    raw_codes = []
    path = Path(path_text)
    if path.is_file():
        raw_codes.extend(line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip())
    if codes_text:
        raw_codes.extend(code.strip() for code in codes_text.split(",") if code.strip())
    result = []
    seen = set()
    for raw_code in raw_codes:
        code = normalize_code(raw_code)
        if not code or code in seen:
            continue
        seen.add(code)
        suffix_upper = suffix.upper()
        if suffix_upper == "AL":
            result.append(f"{code}_AL")
        elif suffix_upper == "NX":
            result.append(f"{code}_NX")
        else:
            result.append(code)
        if limit > 0 and len(result) >= limit:
            break
    if not result:
        raise RuntimeError("no codes to register")
    return result


def publish_collector_status(sender: EventSender, provider, extra: dict[str, Any] | None = None) -> None:
    try:
        provider_status = provider.status()
    except Exception as error:
        provider_status = {"error": str(error)}
    sender_stats = sender.stats()
    payload = {
        "type": "collector_status",
        "ts": now_text(),
        "status": provider_status,
        "sender_stats": sender_stats,
        "sender_sent_count": sender_stats.get("sent_count"),
        "sender_last_error": sender_stats.get("last_error"),
    }
    if extra:
        payload.update(extra)
    sender.publish_direct(payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard v2 32-bit collector adapter")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--event-port", type=int, default=DEFAULT_EVENT_PORT)
    parser.add_argument("--codes-file", default=str(ROOT / "data" / "runtime" / "stockboard_v2" / "codes.txt"))
    parser.add_argument("--codes", default="")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--suffix", default="AL")
    parser.add_argument("--orderbook", action="store_true")
    parser.add_argument("--flush-ms", type=int, default=int(os.getenv("STOCKBOARD_V2_COLLECTOR_FLUSH_MS", "50")))
    args = parser.parse_args()

    if args.orderbook:
        os.environ.setdefault("STOCKBOARD_ENABLE_ORDERBOOK_REALTIME", "1")
        os.environ.setdefault("STOCKBOARD_ORDERBOOK_MODE", "hybrid")
        os.environ.setdefault("STOCKBOARD_ORDERBOOK_HOT_SOURCE", "top5")
        os.environ.setdefault("STOCKBOARD_ORDERBOOK_HOT_LIMIT", "5")
        os.environ.setdefault("STOCKBOARD_ORDERBOOK_ROTATE_BATCH", "20")
        os.environ.setdefault("STOCKBOARD_ORDERBOOK_ROTATE_INTERVAL_SEC", "5")
    os.environ.setdefault("STOCKBOARD_PRICE_FAST_MODE", "1")
    os.environ.setdefault("STOCKBOARD_REALTIME_CODE_LIMIT", str(max(1, int(args.limit or 300))))

    sender = EventSender(args.host, args.event_port, flush_ms=args.flush_ms)
    sender.start()
    store = PublishingStore(sender)
    provider = KiwoomOpenApiRealtimeProvider(store=store)
    codes = load_codes(args.codes_file, args.codes, args.limit, args.suffix)
    print(f"collector codes={len(codes)} suffix={args.suffix} orderbook={args.orderbook} flush_ms={args.flush_ms}", flush=True)
    started = provider.start()
    print(f"provider_start={started}", flush=True)
    if not started:
        publish_collector_status(sender, provider, {"provider_started": False})
        print(f"provider_status={provider.status()}", flush=True)
        time.sleep(0.2)
        return 1
    registered_count = provider.register_codes(codes)
    print(f"registered_count={registered_count}", flush=True)
    try:
        while True:
            publish_collector_status(sender, provider, {"provider_started": True, "registered_count": registered_count})
            time.sleep(1.0)
    except KeyboardInterrupt:
        return 0
    finally:
        sender.stop()
        try:
            provider.stop()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
