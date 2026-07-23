from __future__ import annotations

"""Bound the full-row SSE stream to a latest-only cadence during live bursts.

The existing stream rebuilt and serialized a full snapshot every 100 ms whenever
``event_count`` changed. At market-open rates that produces a browser/network
backlog: old payloads are delivered in order even though only the newest quote is
useful. This patch reads the cheap event counter first and builds a snapshot only
when the latest-only send window opens.

No QAx, FID, collector, REST, WebSocket, ranking, calculation, or browser render
contract changes. The browser still receives the same ``snapshot`` SSE event and
at most 100 rows; intermediate full snapshots are coalesced.
"""

import time
from typing import Any

PATCH_VERSION = "sse_latest_only_v1"
DEFAULT_SEND_INTERVAL_MS = 200
HEARTBEAT_SEC = 2.0


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int(query.get(key, [str(default)])[0])
    except (TypeError, ValueError, IndexError):
        return default


def install(base) -> None:
    handler_class = getattr(base, "WebHandler", None)
    if handler_class is None or getattr(handler_class, "_stockboard_sse_latest_only_installed", False):
        return

    def stream_latest_only(self, query: dict[str, list[str]]) -> None:
        requested_limit = _query_int(query, "limit", 100)
        limit = max(1, min(100, requested_limit))
        requested_interval_ms = _query_int(query, "interval_ms", 100)
        send_interval_ms = max(DEFAULT_SEND_INTERVAL_MS, min(2000, requested_interval_ms))
        send_interval_sec = send_interval_ms / 1000.0
        poll_sec = min(0.05, send_interval_sec)

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        last_event_count: Any = None
        last_sent_at = 0.0
        sent_count = 0
        coalesced_count = 0

        with self.server.state.lock:
            state = self.server.state
            state.status["stream_clients"] = int(state.status.get("stream_clients") or 0) + 1
            state.status["sse_latest_only_version"] = PATCH_VERSION
            state.status["sse_latest_only_send_interval_ms"] = send_interval_ms
            state.status["sse_latest_only_row_limit"] = limit

        try:
            while True:
                now = time.monotonic()
                with self.server.state.lock:
                    event_count = self.server.state.status.get("event_count")

                changed = event_count != last_event_count
                send_due = changed and (now - last_sent_at) >= send_interval_sec
                heartbeat_due = (now - last_sent_at) >= HEARTBEAT_SEC

                if send_due or heartbeat_due:
                    snapshot = self.server.state.snapshot(limit=limit)
                    body = base.safe_json_dumps(snapshot)
                    self.wfile.write(f"event: snapshot\ndata: {body}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    last_event_count = snapshot.get("status", {}).get("event_count", event_count)
                    last_sent_at = time.monotonic()
                    sent_count += 1
                    with self.server.state.lock:
                        self.server.state.status["sse_latest_only_sent_count"] = sent_count
                        self.server.state.status["sse_latest_only_last_sent_at"] = base.now_text()
                        self.server.state.status["sse_latest_only_coalesced_count"] = coalesced_count
                elif changed:
                    coalesced_count += 1

                time.sleep(poll_sec)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
        finally:
            with self.server.state.lock:
                self.server.state.status["stream_clients"] = max(
                    0, int(self.server.state.status.get("stream_clients") or 1) - 1
                )
                self.server.state.status["sse_latest_only_coalesced_count"] = coalesced_count

    handler_class._stream_snapshots = stream_latest_only
    handler_class._stockboard_sse_latest_only_installed = True


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker64 as base

    install(base)
