from __future__ import annotations

"""Run the operator trace against the browser's real fast-price SSE contract.

The browser keeps the heavy snapshot/display payload at 100 rows, but its dedicated
``/api/v2/price-stream`` subscribes with ``limit=300``.  The price stream is a delta
stream, so repeated Collector events with the same price must not all be matched to
one later SSE row.  This wrapper changes only the operator diagnostic.
"""

import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import stockboard_v2_price_trace as trace

BROWSER_SNAPSHOT_ROW_LIMIT = 100
BROWSER_PRICE_STREAM_ROW_LIMIT = 300


def _browser_stream_url(snapshot_url: str, interval_ms: int) -> str:
    parsed = urlparse(snapshot_url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/api/v2/price-stream",
            "",
            urlencode(
                {
                    "limit": BROWSER_PRICE_STREAM_ROW_LIMIT,
                    "interval_ms": max(50, min(1000, int(interval_ms))),
                    "ts": int(time.time() * 1000),
                }
            ),
            "",
        )
    )


def _changed_events(events: list[dict]) -> list[dict]:
    """Keep only price/rate state transitions from the Collector event sequence."""
    result: list[dict] = []
    previous: tuple[object, object] | None = None
    for event in sorted(events, key=lambda item: str(item.get("event_ts") or "")):
        current = (trace._price(event.get("price")), trace._number(event.get("change_rate")))
        if current != previous:
            result.append(event)
            previous = current
    return result


def _one_to_one_matched_delays_ms(events: list[dict], rows: list[dict]) -> list[float]:
    """Match each price transition to the first later, still-unmatched SSE delta row."""
    changed_events = _changed_events(events)
    unique_rows = sorted(
        trace._latest_unique_rows(rows), key=lambda item: str(item.get("observed_at") or "")
    )
    delays: list[float] = []
    next_row_index = 0

    for event in changed_events:
        event_ts = trace._timestamp(event.get("event_ts"))
        if event_ts is None:
            continue
        for index in range(next_row_index, len(unique_rows)):
            row = unique_rows[index]
            observed_ts = trace._timestamp(row.get("observed_at"))
            if observed_ts is None or observed_ts < event_ts:
                continue
            if trace._same_price_rate(event, row):
                delays.append(max(0.0, observed_ts - event_ts) * 1000.0)
                next_row_index = index + 1
                break
    return delays


def main() -> int:
    trace.DEFAULT_SNAPSHOT_URL = (
        f"http://127.0.0.1:8765/api/v2/snapshot?limit={BROWSER_SNAPSHOT_ROW_LIMIT}"
    )
    trace._stream_url = _browser_stream_url
    trace._matched_delays_ms = _one_to_one_matched_delays_ms
    return trace.main()


if __name__ == "__main__":
    raise SystemExit(main())
