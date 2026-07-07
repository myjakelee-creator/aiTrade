from __future__ import annotations

import argparse
import json
import queue
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kiwoom_data_provider import KiwoomOpenApiRealtimeProvider  # noqa: E402
from realtime_v2.common import DEFAULT_EVENT_PORT, DEFAULT_HOST, normalize_code, now_text, safe_json_dumps  # noqa: E402


class EventSender(threading.Thread):
    def __init__(self, host: str, port: int, events: "queue.SimpleQueue[dict[str, Any]]"):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.events = events
        self.stop_event = threading.Event()
        self.sent_count = 0
        self.last_error = None

    def run(self) -> None:
        sock = None
        while not self.stop_event.is_set():
            if sock is None:
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=1.0)
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.last_error = None
                except OSError as error:
                    self.last_error = str(error)
                    time.sleep(0.5)
                    continue
            try:
                event = self.events.get(timeout=0.2)
            except Exception:
                continue
            try:
                sock.sendall(safe_json_dumps(event).encode("utf-8") + b"\n")
                self.sent_count += 1
            except OSError as error:
                self.last_error = str(error)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None

    def stop(self) -> None:
        self.stop_event.set()


class PublishingStore:
    def __init__(self, events: "queue.SimpleQueue[dict[str, Any]]"):
        self.events = events

    def update_trade(self, stock_code, values=None, **kwargs):
        self.events.put(
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
        self.events.put(
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
        self.events.put(
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


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard v2 32-bit collector adapter")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--event-port", type=int, default=DEFAULT_EVENT_PORT)
    parser.add_argument("--codes-file", default=str(ROOT / "data" / "runtime" / "stockboard_v2" / "codes.txt"))
    parser.add_argument("--codes", default="")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--suffix", default="AL")
    args = parser.parse_args()

    events: "queue.SimpleQueue[dict[str, Any]]" = queue.SimpleQueue()
    sender = EventSender(args.host, args.event_port, events)
    sender.start()
    store = PublishingStore(events)
    provider = KiwoomOpenApiRealtimeProvider(store=store)
    codes = load_codes(args.codes_file, args.codes, args.limit, args.suffix)
    print(f"collector codes={len(codes)} suffix={args.suffix}", flush=True)
    provider.start(codes)
    try:
        while True:
            status = provider.status()
            events.put({"type": "collector_status", "ts": now_text(), "status": status, "sender_sent_count": sender.sent_count, "sender_last_error": sender.last_error})
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
