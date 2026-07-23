from __future__ import annotations

"""Add a lightweight latest-only SSE path for price and change-rate cells.

The full StockBoard snapshot performs ranking, candidate projection, display policy,
row copying, and JSON serialization. That path remains authoritative for the whole
board, but it is too heavy to be the transport for every price tick during a burst.

This patch adds ``/api/v2/price-stream``. It reads only scalar quote fields already
accepted by the Worker and never calls ``State.rows()`` or ``State.snapshot()``.
The browser applies these rows through the existing price/rate DOM fast-patch while
the full stream continues at a slower cadence for ranking and auxiliary metrics.

No QAx, FID, Collector, EventSender, REST, WebSocket, ranking, trade-value,
orderbook, or auxiliary-metric calculation is added or changed.
"""

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

PATCH_VERSION = "price_fast_sse_v1"
DEFAULT_INTERVAL_MS = 100
DEFAULT_ROW_LIMIT = 300
HEARTBEAT_SEC = 2.0
_UI_MARKER = "STOCKBOARD_V2_PRICE_FAST_SSE_20260723"
_UI_ANCHOR = (
    "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});"
    "loadCandidateModels();loadContext();markSortHeaders();connectStream();"
)

_UI_PATCH = r"""
  /* STOCKBOARD_V2_PRICE_FAST_SSE_20260723 */
  let __sbv2PriceFastStream = null;
  let __sbv2PriceFastLagMs = null;
  let __sbv2FullStreamLagMs = null;

  function __sbv2UpdateTransportLabel(){
    const priceText = __sbv2PriceFastLagMs === null ? '-' : `${Math.round(__sbv2PriceFastLagMs)} ms`;
    const fullText = __sbv2FullStreamLagMs === null ? '-' : `${Math.round(__sbv2FullStreamLagMs)} ms`;
    latencyEl.textContent = `price ${priceText} · full ${fullText} · sort ${sortState.key}/${sortState.dir}`;
  }

  const __sbv2PriceFastOriginalRender = render;
  render = function(payload, mode='stream', opt={}){
    __sbv2FullStreamLagMs = payloadLagMs(payload);
    const result = __sbv2PriceFastOriginalRender(payload, mode, opt);
    __sbv2UpdateTransportLabel();
    return result;
  };

  function __sbv2ConnectPriceFastStream(){
    if(!window.EventSource || __sbv2PriceFastStream) return;
    __sbv2PriceFastStream = new EventSource(`/api/v2/price-stream?limit=300&interval_ms=100&ts=${Date.now()}`);
    __sbv2PriceFastStream.addEventListener('price', event => {
      try{
        const payload = JSON.parse(event.data);
        __sbv2PriceFastLagMs = payloadLagMs(payload);
        __sbv2FastPatchPriceRate(payload);
        __sbv2UpdateTransportLabel();
      }catch(error){
        console.warn(error);
      }
    });
    __sbv2PriceFastStream.onerror = () => {
      /* EventSource reconnects automatically; the authoritative full stream remains active. */
    };
  }

  __sbv2ConnectPriceFastStream();
"""


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(query.get(key, [str(default)])[0])
    except (TypeError, ValueError, IndexError):
        return default


def build_price_snapshot(state: Any, *, limit: int, now_text) -> dict[str, Any]:
    """Copy only price-path scalars; deliberately never call rows()/snapshot()."""

    safe_limit = max(1, min(DEFAULT_ROW_LIMIT, int(limit)))
    with state.lock:
        quotes = list(getattr(state, "quotes", {}).items())
        trade_count = int(getattr(state, "status", {}).get("trade_count") or 0)

    rows: list[dict[str, Any]] = []
    for code, quote in quotes:
        if not isinstance(quote, dict):
            continue
        rows.append(
            {
                "stock_code": str(quote.get("stock_code") or code or ""),
                "price": quote.get("price") if quote.get("price") is not None else quote.get("trade_price"),
                "change_rate": quote.get("change_rate"),
                "received_at": quote.get("received_at"),
            }
        )

    rows.sort(key=lambda row: row.get("stock_code") or "")
    rows = rows[:safe_limit]
    return {
        "schema_version": 1,
        "source": "stockboard_v2_price_fast_sse",
        "ts": now_text(),
        "trade_count": trade_count,
        "row_count": len(rows),
        "rows": rows,
    }


def _install_state(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(state_class, "_stockboard_price_fast_sse_installed", False):
        return

    def price_fast_snapshot(self, limit: int = DEFAULT_ROW_LIMIT) -> dict[str, Any]:
        return build_price_snapshot(self, limit=limit, now_text=base.now_text)

    state_class.price_fast_snapshot = price_fast_snapshot
    state_class._stockboard_price_fast_sse_installed = True
    state_class._stockboard_price_fast_sse_version = PATCH_VERSION


def _install_web_handler(base) -> None:
    handler_class = getattr(base, "WebHandler", None)
    if handler_class is None or getattr(handler_class, "_stockboard_price_fast_sse_installed", False):
        return

    original_do_get = handler_class.do_GET

    def stream_price_fast(self, query: dict[str, list[str]]) -> None:
        requested_limit = _query_int(query, "limit", DEFAULT_ROW_LIMIT)
        limit = max(1, min(DEFAULT_ROW_LIMIT, requested_limit))
        requested_interval_ms = _query_int(query, "interval_ms", DEFAULT_INTERVAL_MS)
        interval_ms = max(50, min(1000, requested_interval_ms))
        interval_sec = interval_ms / 1000.0
        poll_sec = min(0.02, interval_sec)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_trade_count: Any = None
        last_sent_at = 0.0
        sent_count = 0
        coalesced_count = 0

        with self.server.state.lock:
            status = self.server.state.status
            status["price_stream_clients"] = int(status.get("price_stream_clients") or 0) + 1
            status["price_fast_sse_version"] = PATCH_VERSION
            status["price_fast_sse_interval_ms"] = interval_ms
            status["price_fast_sse_row_limit"] = limit

        try:
            while True:
                now = time.monotonic()
                with self.server.state.lock:
                    trade_count = self.server.state.status.get("trade_count")

                changed = trade_count != last_trade_count
                send_due = changed and (now - last_sent_at) >= interval_sec
                heartbeat_due = (now - last_sent_at) >= HEARTBEAT_SEC

                if send_due or heartbeat_due:
                    payload = self.server.state.price_fast_snapshot(limit=limit)
                    body = base.safe_json_dumps(payload)
                    self.wfile.write(f"event: price\ndata: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_trade_count = payload.get("trade_count", trade_count)
                    last_sent_at = time.monotonic()
                    sent_count += 1
                    with self.server.state.lock:
                        status = self.server.state.status
                        status["price_fast_sse_sent_count"] = sent_count
                        status["price_fast_sse_coalesced_count"] = coalesced_count
                        status["price_fast_sse_last_sent_at"] = base.now_text()
                        status["price_fast_sse_last_row_count"] = payload.get("row_count")
                elif changed:
                    coalesced_count += 1

                time.sleep(poll_sec)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with self.server.state.lock:
                status = self.server.state.status
                status["price_stream_clients"] = max(
                    0, int(status.get("price_stream_clients") or 1) - 1
                )
                status["price_fast_sse_coalesced_count"] = coalesced_count

    def do_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/v2/price-stream":
            stream_price_fast(self, parse_qs(parsed.query))
            return
        original_do_get(self)

    handler_class._stream_price_fast = stream_price_fast
    handler_class.do_GET = do_get
    handler_class._stockboard_price_fast_sse_installed = True


def _install_ui(large) -> None:
    if large is None or getattr(large, "_stockboard_price_fast_sse_ui_installed", False):
        return

    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if _UI_MARKER in patched or _UI_ANCHOR not in patched:
            return patched

        # Full-row snapshots remain authoritative but no longer compete with the
        # 100 ms price-only path during live bursts.
        patched = patched.replace(
            "/api/v2/stream?limit=100&interval_ms=100&ts=${Date.now()}",
            "/api/v2/stream?limit=100&interval_ms=1000&ts=${Date.now()}",
        )
        return patched.replace(_UI_ANCHOR, f"{_UI_PATCH}\n{_UI_ANCHOR}", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._stockboard_price_fast_sse_ui_installed = True


def install(base, large=None) -> None:
    _install_state(base)
    _install_web_handler(base)
    _install_ui(large)
