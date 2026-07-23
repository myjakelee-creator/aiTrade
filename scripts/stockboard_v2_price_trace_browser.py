from __future__ import annotations

"""Run the operator price trace with the same 100-row payload used by the browser.

The original trace predated the display-100 optimization and hard-coded a 300-row SSE
request. During market-open load that diagnostic request could time out even while the
actual browser stream remained active. This wrapper changes only the operator trace URL;
it is never imported by the Worker or Collector.
"""

import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import stockboard_v2_price_trace as trace

BROWSER_ROW_LIMIT = 100


def _browser_stream_url(snapshot_url: str, interval_ms: int) -> str:
    parsed = urlparse(snapshot_url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/api/v2/stream",
            "",
            urlencode(
                {
                    "limit": BROWSER_ROW_LIMIT,
                    "interval_ms": max(50, min(2000, int(interval_ms))),
                    "ts": int(time.time() * 1000),
                }
            ),
            "",
        )
    )


def main() -> int:
    trace.DEFAULT_SNAPSHOT_URL = (
        f"http://127.0.0.1:8765/api/v2/snapshot?limit={BROWSER_ROW_LIMIT}"
    )
    trace._stream_url = _browser_stream_url
    return trace.main()


if __name__ == "__main__":
    raise SystemExit(main())
