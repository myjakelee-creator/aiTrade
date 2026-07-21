"""Loopback-only, allowlisted, read-only public boundary for StockBoard v2."""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
DEFAULT_UPSTREAM = "http://127.0.0.1:8765"
DEFAULT_SNAPSHOT_INTERVAL_SEC = 1.0
DEFAULT_CONTEXT_INTERVAL_SEC = 15.0
DEFAULT_REQUESTS_PER_MINUTE = 240
DEFAULT_GLOBAL_REQUESTS_PER_MINUTE = 2400
DEFAULT_MAX_CONCURRENT_REQUESTS = 64
DEFAULT_MAX_STREAM_CLIENTS = 20

ROW_FIELDS = tuple("""stock_code stock_name rank rank_change candidate_grade_text grade_text grade candidate_grade candidate_score grade_score score_percent price trade_price change_rate trade_value_eok prev_trade_value_eok amount_ratio open open_price day_open high high_price day_high low low_price day_low close bid_ask_ratio execution_strength strength_1m program_net large_trade_net_count price_age_sec""".split())
OHLC_FIELDS = ("open", "high", "low", "close", "current")
SESSION_FIELDS = ("phase", "phase_label", "trading_date", "calendar_date", "is_trading_day")
STATUS_FIELDS = ("market_phase", "market_phase_label", "market_trading_date")
MARKET_FIELDS = tuple("""market_index index 지수 market_change_rate change_rate 등락률 advancers advance 상승 decliners decline 하락 upper_limit_count upper_limit 상한 lower_limit_count lower_limit 하한 individual_eok individual 개인 foreign_spot_eok foreign 외인 institution_eok institution 기관 program_market_eok program 프로""".split())
US_FIELDS = ("change_rate", "change_percent", "regularMarketChangePercent", "rate", "pct")
US_SYMBOLS = ("NQ=F", "ES=F", "YM=F", "QQQ", "SOXL", "SMH", "IBB", "LIT", "BOTZ")
FORBIDDEN_KEYS = set("""pid collector_status daily_state_path event_log_path last_error program_net_last_error candidate_grade_last_error ohlc_snapshot_source strength_snapshot_source previous_daily_display_source context_writer_owner_pid source_code registered_code received_code raw kwargs values_raw""".split())
CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; font-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"

HTML_INJECTION = r"""
<style id="stockboard-public-readonly-style">
#row-position-toggle,#candidate-model-selector,label:has(#candidate-model-selector),#counts,#throughput,#collector-metrics,#worker-metrics,#lag-metrics{display:none!important}
.public-readonly-badge{color:#7c2d12;border-color:#fdba74;background:#ffedd5;font-weight:800}
</style>
<script id="stockboard-public-readonly-script">
(function(){
 const title=document.querySelector('.title');if(title)title.textContent='StockBoard v2 Public';
 const selector=document.getElementById('candidate-model-selector');if(selector){selector.disabled=true;const label=selector.closest('label');if(label)label.style.display='none'}
 const toggle=document.getElementById('row-position-toggle');if(toggle)toggle.style.display='none';
 const status=document.getElementById('status');if(status){const b=document.createElement('span');b.className='badge public-readonly-badge';b.textContent='공개 읽기 전용';status.insertAdjacentElement('afterend',b)}
 if(typeof window.sendHtsCommand==='function')window.sendHtsCommand=function(code){const text=String(code||'').trim();if(!/^\d{6}$/.test(text))return;writeClipboardText(text).then(()=>{const c=document.getElementById('copy-status');if(c){c.textContent=`종목코드 ${text} 복사`;c.className='badge copy-ok'}}).catch(()=>{})};
 const copy=document.getElementById('copy-status');if(copy)copy.textContent='행 클릭 시 종목코드 복사 · 서버 제어 기능 없음';
})();
</script>
""".strip()


def now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _scalar(value: Any) -> Any:
    return value if value is None or isinstance(value, (str, int, float, bool)) else None


def _allowed(source: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    result: dict[str, Any] = {}
    for key in fields:
        if key not in source:
            continue
        value = _scalar(source[key])
        if value is not None or source[key] is None:
            result[key] = value
    return result


def sanitize_row(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    row = _allowed(raw, ROW_FIELDS)
    for key in ("ohlc", "realtime_ohlc", "display_ohlc"):
        nested = _allowed(raw.get(key), OHLC_FIELDS)
        if nested:
            row[key] = nested
    return row


def sanitize_snapshot(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    rows = [sanitize_row(item) for item in raw.get("rows", []) if isinstance(item, dict)]
    rows = [row for row in rows if row.get("stock_code") or row.get("stock_name")]
    status = _allowed(raw.get("status"), STATUS_FIELDS)
    status["public_gateway_state"] = "ok"
    display = raw.get("display_order") if isinstance(raw.get("display_order"), dict) else {}
    return {
        "schema_version": 1,
        "source": "stockboard_v2_public_gateway",
        "ts": _scalar(raw.get("ts")) or now_text(),
        "trading_date": _scalar(raw.get("trading_date")),
        "status": status,
        "market_session": _allowed(raw.get("market_session"), SESSION_FIELDS),
        "display_order": {"paused": bool(display.get("paused"))},
        "row_count": len(rows),
        "rows": rows,
    }


def _lookup(source: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in source:
            return source[name]
    lower = {str(key).lower(): value for key, value in source.items()}
    return next((lower[name.lower()] for name in names if name.lower() in lower), None)


def sanitize_market_supply(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    source = source.get("values") if isinstance(source.get("values"), dict) else source
    result = {}
    for key, names in (("kospi", ("kospi", "KOSPI", "코스피")), ("kosdaq", ("kosdaq", "KOSDAQ", "코스닥"))):
        row = _allowed(_lookup(source, *names), MARKET_FIELDS)
        if row:
            result[key] = row
    return result


def sanitize_us_market(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    source = source.get("values") if isinstance(source.get("values"), dict) else source
    result = {}
    for symbol in US_SYMBOLS:
        value = _lookup(source, symbol, symbol.lower()) if isinstance(source, dict) else None
        if isinstance(value, (int, float)):
            result[symbol] = {"change_rate": value}
        else:
            row = _allowed(value, US_FIELDS)
            if row:
                result[symbol] = row
    return {"values": result}


def sanitize_context(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {"schema_version": 1, "source": "stockboard_v2_public_gateway", "ts": _scalar(raw.get("ts")) or now_text(), "market_supply": sanitize_market_supply(raw.get("market_supply")), "us_market": sanitize_us_market(raw.get("us_market"))}


def contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in FORBIDDEN_KEYS or contains_forbidden_key(nested) for key, nested in value.items())
    return isinstance(value, list) and any(contains_forbidden_key(item) for item in value)


def build_public_html(private_html: str) -> str:
    html = private_html.replace("<title>StockBoard v2 Realtime</title>", "<title>StockBoard v2 Public</title>", 1)
    return html.replace("</body>", f"{HTML_INJECTION}\n</body>", 1) if "</body>" in html else f"{html}\n{HTML_INJECTION}\n"


def load_public_html(path: Path) -> bytes:
    return build_public_html(path.read_text(encoding="utf-8-sig")).encode("utf-8")


class SlidingWindowRateLimiter:
    def __init__(self, per_client_per_minute: int, global_per_minute: int):
        self.client_limit = max(1, int(per_client_per_minute))
        self.global_limit = max(self.client_limit, int(global_per_minute))
        self.lock = threading.Lock()
        self.clients: dict[str, deque[float]] = defaultdict(deque)
        self.global_events: deque[float] = deque()
        self.check_count = 0

    @staticmethod
    def _trim(events: deque[float], now: float) -> None:
        while events and events[0] <= now - 60.0:
            events.popleft()

    def allow(self, client_id: str) -> bool:
        now = time.monotonic()
        with self.lock:
            self.check_count += 1
            if self.check_count % 256 == 0:
                for key, events in list(self.clients.items()):
                    self._trim(events, now)
                    if not events:
                        self.clients.pop(key, None)
            client = self.clients[client_id or "unknown"]
            self._trim(client, now)
            self._trim(self.global_events, now)
            if len(client) >= self.client_limit or len(self.global_events) >= self.global_limit:
                return False
            client.append(now)
            self.global_events.append(now)
            return True


class PublicDataCache:
    def __init__(self, upstream: str, snapshot_interval_sec: float = DEFAULT_SNAPSHOT_INTERVAL_SEC, context_interval_sec: float = DEFAULT_CONTEXT_INTERVAL_SEC, timeout_sec: float = 3.0):
        parsed = urlparse(upstream)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Public gateway upstream must be a loopback HTTP URL.")
        self.upstream = upstream.rstrip("/")
        self.snapshot_interval_sec = max(0.2, float(snapshot_interval_sec))
        self.context_interval_sec = max(5.0, float(context_interval_sec))
        self.timeout_sec = max(0.5, float(timeout_sec))
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.snapshot: dict[str, Any] | None = None
        self.context = sanitize_context({})
        self.version = 0
        self.snapshot_at: float | None = None
        self.context_at: float | None = None
        self.snapshot_error: str | None = None
        self.context_error: str | None = None

    def start(self) -> None:
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name="stockboard-public-cache", daemon=True)
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3.0)

    def _fetch(self, path: str) -> dict[str, Any]:
        request = Request(f"{self.upstream}{path}", headers={"Accept": "application/json", "User-Agent": "StockBoardPublicGateway/1.0"})
        with urlopen(request, timeout=self.timeout_sec) as response:
            body = response.read(8 * 1024 * 1024 + 1)
            if response.status != 200 or len(body) > 8 * 1024 * 1024:
                raise RuntimeError("invalid upstream response")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("upstream response is not an object")
            return payload

    def refresh_snapshot(self) -> None:
        value = sanitize_snapshot(self._fetch("/api/v2/snapshot?limit=300"))
        if contains_forbidden_key(value):
            raise RuntimeError("sanitizer emitted a forbidden key")
        with self.condition:
            self.snapshot, self.snapshot_at, self.snapshot_error = value, time.monotonic(), None
            self.version += 1
            self.condition.notify_all()

    def refresh_context(self) -> None:
        value = sanitize_context(self._fetch("/api/v2/context"))
        if contains_forbidden_key(value):
            raise RuntimeError("context sanitizer emitted a forbidden key")
        with self.lock:
            self.context, self.context_at, self.context_error = value, time.monotonic(), None

    def _run(self) -> None:
        handled = (HTTPError, URLError, OSError, ValueError, RuntimeError, json.JSONDecodeError)
        next_context = 0.0
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.refresh_snapshot()
            except handled as error:
                with self.lock:
                    self.snapshot_error = str(error)
            if started >= next_context:
                try:
                    self.refresh_context()
                except handled as error:
                    with self.lock:
                        self.context_error = str(error)
                next_context = started + self.context_interval_sec
            self.stop_event.wait(max(0.05, self.snapshot_interval_sec - (time.monotonic() - started)))

    def get_snapshot(self) -> tuple[dict[str, Any] | None, int]:
        with self.lock:
            return self.snapshot, self.version

    def wait_for_snapshot(self, version: int, timeout_sec: float) -> tuple[dict[str, Any] | None, int]:
        with self.condition:
            if self.version <= version and not self.stop_event.is_set():
                self.condition.wait(timeout=max(0.05, timeout_sec))
            return self.snapshot, self.version

    def get_context(self) -> dict[str, Any]:
        with self.lock:
            return self.context

    def health(self) -> dict[str, Any]:
        with self.lock:
            now = time.monotonic()
            snapshot_age = None if self.snapshot_at is None else round(now - self.snapshot_at, 3)
            context_age = None if self.context_at is None else round(now - self.context_at, 3)
            upstream_ok = self.snapshot is not None and self.snapshot_error is None and snapshot_age is not None and snapshot_age <= max(5.0, self.snapshot_interval_sec * 10)
            context_ok = self.context_error is None and context_age is not None and context_age <= max(60.0, self.context_interval_sec * 4)
            return {"ok": self.snapshot is not None, "service": "stockboard_v2_public_gateway", "read_only": True, "upstream_ok": upstream_ok, "context_ok": context_ok, "snapshot_age_sec": snapshot_age, "ts": now_text()}


class PublicGatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, cache, public_html: bytes, per_client_rate: int, global_rate: int, max_concurrent_requests: int, max_stream_clients: int):
        super().__init__(address, handler)
        self.cache, self.public_html = cache, public_html
        self.rate_limiter = SlidingWindowRateLimiter(per_client_rate, global_rate)
        self.request_slots = threading.BoundedSemaphore(max(1, max_concurrent_requests))
        self.stream_slots = threading.BoundedSemaphore(max(1, max_stream_clients))
        self.stream_lock, self.active_stream_clients = threading.Lock(), 0

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(30.0)
        return request, address

    def stream_opened(self):
        with self.stream_lock:
            self.active_stream_clients += 1

    def stream_closed(self):
        with self.stream_lock:
            self.active_stream_clients = max(0, self.active_stream_clients - 1)


class PublicGatewayHandler(BaseHTTPRequestHandler):
    server_version, sys_version, protocol_version = "StockBoardPublic/1.0", "", "HTTP/1.1"

    def _client_id(self) -> str:
        value = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()[:64]
        return value if value and all(char.isalnum() or char in ".:-" for char in value) else str(self.client_address[0])

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{now_text()} client={self._client_id()} {fmt % args}", flush=True)

    def _headers(self):
        for key, value in {
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()", "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin", "X-Robots-Tag": "noindex, nofollow, noarchive", "Content-Security-Policy": CSP,
            "Cache-Control": "no-store",
        }.items():
            self.send_header(key, value)

    def _bytes(self, body: bytes, content_type: str, status=200, head=False):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._headers()
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _json(self, value: Any, status=200, head=False):
        self._bytes(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(), "application/json; charset=utf-8", status, head)

    def _error(self, status, code, head=False):
        self._json({"error": code}, status, head)

    def _read_only(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            content_length = 0
        if 0 < content_length <= 1024 * 1024:
            self.rfile.read(content_length)
        self.close_connection = True
        self._error(405, "read_only_gateway")

    def do_POST(self): self._read_only()
    def do_PUT(self): self._read_only()
    def do_PATCH(self): self._read_only()
    def do_DELETE(self): self._read_only()
    def do_OPTIONS(self): self._read_only()
    def do_HEAD(self): self._get(True)
    def do_GET(self): self._get(False)

    def _get(self, head: bool):
        if not self.server.request_slots.acquire(False):
            self._error(503, "server_busy", head); return
        try:
            if not self.server.rate_limiter.allow(self._client_id()):
                self._error(429, "rate_limited", head); return
            path = urlparse(self.path).path
            if path in {"/", "/public", "/stockboard_public.html"}:
                self._bytes(self.server.public_html, "text/html; charset=utf-8", head=head); return
            if path == "/robots.txt":
                self._bytes(b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8", head=head); return
            if path == "/api/v2/health":
                value = self.server.cache.health(); value["active_stream_clients"] = self.server.active_stream_clients
                self._json(value, 200 if value["ok"] else 503, head); return
            if path == "/api/v2/snapshot":
                value, _ = self.server.cache.get_snapshot()
                self._json(value, head=head) if value is not None else self._error(503, "snapshot_unavailable", head); return
            if path == "/api/v2/context":
                self._json(self.server.cache.get_context(), head=head); return
            if path == "/api/v2/candidate_models":
                self._json({"schema_version": 1, "default_model_id": "public_read_only", "models": [{"id": "public_read_only", "label": "공개 읽기 전용"}]}, head=head); return
            if path == "/api/v2/stream":
                self._error(405, "stream_requires_get", True) if head else self._stream(); return
            if path == "/api/v2/display_order":
                self._error(403, "server_control_not_public", head); return
            self._error(404, "not_found", head)
        finally:
            self.server.request_slots.release()

    def _stream(self):
        if not self.server.stream_slots.acquire(False):
            self._error(503, "stream_capacity_reached"); return
        self.server.stream_opened()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._headers(); self.end_headers()
            version = -1
            while True:
                value, next_version = self.server.cache.wait_for_snapshot(version, 10.0)
                if value is not None and next_version != version:
                    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(f"event: snapshot\ndata: {data}\n\n".encode()); version = next_version
                else:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.stream_closed(); self.server.stream_slots.release()


def _int(value, default, minimum, maximum):
    try: value = int(value) if value not in (None, "") else default
    except (TypeError, ValueError): value = default
    return max(minimum, min(maximum, value))


def _float(value, default, minimum, maximum):
    try: value = float(value) if value not in (None, "") else default
    except (TypeError, ValueError): value = default
    return max(minimum, min(maximum, value))


def main() -> int:
    parser = argparse.ArgumentParser(description="StockBoard v2 public read-only gateway")
    parser.add_argument("--host", default=os.getenv("STOCKBOARD_PUBLIC_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=_int(os.getenv("STOCKBOARD_PUBLIC_PORT"), DEFAULT_PORT, 1, 65535))
    parser.add_argument("--upstream", default=os.getenv("STOCKBOARD_PUBLIC_UPSTREAM", DEFAULT_UPSTREAM))
    parser.add_argument("--html", default=str(ROOT / "docs" / "stockboard_v2.html"))
    parser.add_argument("--snapshot-interval-sec", type=float, default=_float(os.getenv("STOCKBOARD_PUBLIC_SNAPSHOT_INTERVAL_SEC"), DEFAULT_SNAPSHOT_INTERVAL_SEC, .2, 5.0))
    parser.add_argument("--context-interval-sec", type=float, default=_float(os.getenv("STOCKBOARD_PUBLIC_CONTEXT_INTERVAL_SEC"), DEFAULT_CONTEXT_INTERVAL_SEC, 5.0, 120.0))
    parser.add_argument("--requests-per-minute", type=int, default=_int(os.getenv("STOCKBOARD_PUBLIC_REQUESTS_PER_MINUTE"), DEFAULT_REQUESTS_PER_MINUTE, 30, 5000))
    parser.add_argument("--global-requests-per-minute", type=int, default=_int(os.getenv("STOCKBOARD_PUBLIC_GLOBAL_REQUESTS_PER_MINUTE"), DEFAULT_GLOBAL_REQUESTS_PER_MINUTE, 100, 50000))
    parser.add_argument("--max-concurrent-requests", type=int, default=_int(os.getenv("STOCKBOARD_PUBLIC_MAX_CONCURRENT_REQUESTS"), DEFAULT_MAX_CONCURRENT_REQUESTS, 4, 512))
    parser.add_argument("--max-stream-clients", type=int, default=_int(os.getenv("STOCKBOARD_PUBLIC_MAX_STREAM_CLIENTS"), DEFAULT_MAX_STREAM_CLIENTS, 1, 200))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Public gateway must bind to loopback.")
    cache = PublicDataCache(args.upstream, args.snapshot_interval_sec, args.context_interval_sec)
    cache.start()
    server = PublicGatewayServer((args.host, args.port), PublicGatewayHandler, cache=cache, public_html=load_public_html(Path(args.html)), per_client_rate=args.requests_per_minute, global_rate=args.global_requests_per_minute, max_concurrent_requests=args.max_concurrent_requests, max_stream_clients=args.max_stream_clients)
    print(f"StockBoard public gateway http://{args.host}:{args.port}/ upstream={args.upstream} read_only=True", flush=True)
    try:
        server.serve_forever(.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close(); cache.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
