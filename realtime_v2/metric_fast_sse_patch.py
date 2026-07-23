from __future__ import annotations

"""Add a lightweight delta SSE for visible non-price StockBoard metrics.

The candidate/grade snapshot is intentionally expensive and may take many seconds.
This transport never calls State.rows()/snapshot(); it copies already-approved scalar
values from State.quotes, computes only the current-day cumulative trade-value rank,
and sends changed rows every 500 ms.  The browser merges those scalars into the last
full payload and re-renders at most once per second.
"""

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

PATCH_VERSION = "metric_fast_sse_v1"
DEFAULT_INTERVAL_MS = 500
DEFAULT_ROW_LIMIT = 300
HEARTBEAT_SEC = 10.0
_UI_MARKER = "STOCKBOARD_V2_METRIC_FAST_SSE_20260723"
_UI_ANCHOR = "clockEl.textContent=new Date().toLocaleTimeString('ko-KR',{hour12:false});loadCandidateModels();loadContext();markSortHeaders();connectStream();"

_METRIC_FIELDS = (
    "trade_value_eok",
    "trade_value_trading_date",
    "amount_ratio",
    "amount_ratio_missing_reason",
    "trade_value_1m_eok",
    "minute_trade_value_eok",
    "bid_ask_ratio",
    "execution_strength",
    "strength_1m",
    "strength_5m",
    "program_net",
    "large_trade_net_count",
    "large_trade_net_sum_eok",
)

_UI_PATCH = r"""
  /* STOCKBOARD_V2_METRIC_FAST_SSE_20260723 */
  let __sbv2MetricFastStream = null;
  let __sbv2MetricFastReconnectTimer = null;
  let __sbv2MetricFastLagMs = null;
  let __sbv2MetricFastRenderTimer = null;

  function __sbv2ScheduleMetricRender(){
    if(__sbv2MetricFastRenderTimer || !lastPayload) return;
    __sbv2MetricFastRenderTimer = setTimeout(() => {
      __sbv2MetricFastRenderTimer = null;
      if(lastPayload) render(lastPayload,'stream',{forcePool:true});
    }, 1000);
  }

  function __sbv2ApplyMetricPayload(payload){
    if(!lastPayload || !Array.isArray(lastPayload.rows) || !Array.isArray(payload?.rows)) return;
    const byCode = new Map(lastPayload.rows.map(row => [String(row.stock_code || ''), row]));
    payload.rows.forEach(update => {
      const row = byCode.get(String(update.stock_code || ''));
      if(row) Object.assign(row, update);
    });
    __sbv2MetricFastLagMs = payloadLagMs(payload);
    __sbv2ScheduleMetricRender();
  }

  function __sbv2ConnectMetricFastStream(){
    if(!window.EventSource) return;
    if(__sbv2MetricFastStream && __sbv2MetricFastStream.readyState !== EventSource.CLOSED) return;
    if(__sbv2MetricFastReconnectTimer){ clearTimeout(__sbv2MetricFastReconnectTimer); __sbv2MetricFastReconnectTimer = null; }
    __sbv2MetricFastStream = new EventSource(`/api/v2/metric-stream?limit=300&interval_ms=500&ts=${Date.now()}`);
    __sbv2MetricFastStream.addEventListener('metric', event => {
      try{ __sbv2ApplyMetricPayload(JSON.parse(event.data)); }catch(error){ console.warn(error); }
    });
    __sbv2MetricFastStream.onerror = () => {
      const failed = __sbv2MetricFastStream;
      __sbv2MetricFastStream = null;
      try{ if(failed) failed.close(); }catch(_error){}
      if(__sbv2MetricFastReconnectTimer) clearTimeout(__sbv2MetricFastReconnectTimer);
      __sbv2MetricFastReconnectTimer = setTimeout(__sbv2ConnectMetricFastStream, 1000);
    };
  }

  __sbv2ConnectMetricFastStream();
"""


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(query.get(key, [str(default)])[0])
    except (TypeError, ValueError, IndexError):
        return default


def _date_digits(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _current_date(state: Any) -> str:
    status = getattr(state, "status", {})
    for key in ("market_trading_date", "board_display_current_trading_date", "board_expected_trading_date"):
        text = _date_digits(status.get(key) if isinstance(status, dict) else None)
        if text:
            return text
    return ""


def build_metric_snapshot(state: Any, *, limit: int, now_text) -> dict[str, Any]:
    safe_limit = max(1, min(DEFAULT_ROW_LIMIT, int(limit)))
    with state.lock:
        quote_items = list(getattr(state, "quotes", {}).items())
        event_count = int(getattr(state, "status", {}).get("event_count") or 0)
        current_date = _current_date(state)

    rows: list[dict[str, Any]] = []
    for code, quote in quote_items:
        if not isinstance(quote, dict):
            continue
        row = {"stock_code": str(quote.get("stock_code") or code or "")}
        for key in _METRIC_FIELDS:
            if key in quote:
                row[key] = quote.get(key)
        rows.append(row)

    eligible = []
    for row in rows:
        value = _number(row.get("trade_value_eok"))
        value_date = _date_digits(row.get("trade_value_trading_date"))
        if current_date and value_date == current_date and value is not None and value >= 0:
            eligible.append(row)
        else:
            row["rank"] = None
    eligible.sort(key=lambda row: (-float(_number(row.get("trade_value_eok")) or 0.0), row.get("stock_code") or ""))
    for rank, row in enumerate(eligible, start=1):
        row["rank"] = rank

    rows.sort(key=lambda row: row.get("stock_code") or "")
    rows = rows[:safe_limit]
    return {
        "schema_version": 2,
        "source": "stockboard_v2_metric_fast_sse",
        "ts": now_text(),
        "event_count": event_count,
        "row_count": len(rows),
        "payload_mode": "full",
        "rows": rows,
    }


def _fingerprint(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in ("rank",) + _METRIC_FIELDS)


def build_delta_payload(payload: dict[str, Any], previous: dict[str, tuple[Any, ...]], *, force_full: bool = False):
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    current: dict[str, tuple[Any, ...]] = {}
    changed = []
    for row in rows:
        code = str(row.get("stock_code") or "")
        if not code:
            continue
        fp = _fingerprint(row)
        current[code] = fp
        if force_full or previous.get(code) != fp:
            changed.append(row)
    outgoing = dict(payload)
    outgoing["payload_mode"] = "full" if force_full else "delta"
    outgoing["rows"] = rows if force_full else changed
    outgoing["row_count"] = len(outgoing["rows"])
    outgoing["total_quote_count"] = len(rows)
    return outgoing, current


def _install_state(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(state_class, "_stockboard_metric_fast_sse_installed", False):
        return

    def metric_fast_snapshot(self, limit: int = DEFAULT_ROW_LIMIT):
        return build_metric_snapshot(self, limit=limit, now_text=base.now_text)

    state_class.metric_fast_snapshot = metric_fast_snapshot
    state_class._stockboard_metric_fast_sse_installed = True


def _install_web_handler(base) -> None:
    handler_class = getattr(base, "WebHandler", None)
    if handler_class is None or getattr(handler_class, "_stockboard_metric_fast_sse_installed", False):
        return
    original_do_get = handler_class.do_GET

    def stream_metric_fast(self, query: dict[str, list[str]]) -> None:
        limit = max(1, min(DEFAULT_ROW_LIMIT, _query_int(query, "limit", DEFAULT_ROW_LIMIT)))
        interval_ms = max(200, min(2000, _query_int(query, "interval_ms", DEFAULT_INTERVAL_MS)))
        interval_sec = interval_ms / 1000.0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        previous: dict[str, tuple[Any, ...]] = {}
        last_event_count: Any = None
        last_sent_at = 0.0
        sent_count = 0
        with self.server.state.lock:
            status = self.server.state.status
            status["metric_stream_clients"] = int(status.get("metric_stream_clients") or 0) + 1
            status["metric_fast_sse_version"] = PATCH_VERSION
            status["metric_fast_sse_interval_ms"] = interval_ms

        try:
            while True:
                now = time.monotonic()
                with self.server.state.lock:
                    event_count = self.server.state.status.get("event_count")
                changed = event_count != last_event_count
                heartbeat_due = (now - last_sent_at) >= HEARTBEAT_SEC
                if (changed and (now - last_sent_at) >= interval_sec) or heartbeat_due:
                    full_payload = self.server.state.metric_fast_snapshot(limit=limit)
                    payload, previous = build_delta_payload(full_payload, previous, force_full=(not previous or heartbeat_due))
                    last_event_count = full_payload.get("event_count", event_count)
                    if payload.get("row_count") or payload.get("payload_mode") == "full":
                        body = base.safe_json_dumps(payload)
                        self.wfile.write(f"event: metric\ndata: {body}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        last_sent_at = time.monotonic()
                        sent_count += 1
                        with self.server.state.lock:
                            status = self.server.state.status
                            status["metric_fast_sse_sent_count"] = sent_count
                            status["metric_fast_sse_last_row_count"] = payload.get("row_count")
                            status["metric_fast_sse_last_payload_mode"] = payload.get("payload_mode")
                            status["metric_fast_sse_last_sent_at"] = base.now_text()
                time.sleep(0.05)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with self.server.state.lock:
                status = self.server.state.status
                status["metric_stream_clients"] = max(0, int(status.get("metric_stream_clients") or 1) - 1)

    def do_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/v2/metric-stream":
            stream_metric_fast(self, parse_qs(parsed.query))
            return
        original_do_get(self)

    handler_class.do_GET = do_get
    handler_class._stream_metric_fast = stream_metric_fast
    handler_class._stockboard_metric_fast_sse_installed = True


def _install_ui(large) -> None:
    if large is None or getattr(large, "_stockboard_metric_fast_sse_ui_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if _UI_MARKER in patched or _UI_ANCHOR not in patched:
            return patched
        patched = patched.replace(
            "/api/v2/stream?limit=100&interval_ms=1000&ts=${Date.now()}",
            "/api/v2/stream?limit=100&interval_ms=5000&ts=${Date.now()}",
        )
        return patched.replace(_UI_ANCHOR, f"{_UI_PATCH}\n{_UI_ANCHOR}", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._stockboard_metric_fast_sse_ui_installed = True


def install(base, large=None) -> None:
    _install_state(base)
    _install_web_handler(base)
    _install_ui(large)


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module
    if getattr(opening_module, "_metric_fast_sse_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        from realtime_v2 import worker64_guarded_large as large
        install(base, large)

    opening_module.install = install_after_opening
    opening_module._metric_fast_sse_install_wrapped = True
