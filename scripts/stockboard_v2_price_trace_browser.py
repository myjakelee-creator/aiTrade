from __future__ import annotations

"""Run the operator trace with the browser's 100-row snapshot boundary.

The underlying trace now opens the actual ``/api/v2/price-stream`` used for visible
price updates.  This wrapper changes only the initial/final snapshot row limit and is
never imported by the Worker or Collector.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import stockboard_v2_price_trace as trace

BROWSER_ROW_LIMIT = 100


def _browser_stream_url(snapshot_url: str, interval_ms: int) -> str:
    """Compatibility helper; delegate to the actual price-stream URL builder."""
    return trace._stream_url(snapshot_url, interval_ms)


def main() -> int:
    trace.DEFAULT_SNAPSHOT_URL = (
        f"http://127.0.0.1:8765/api/v2/snapshot?limit={BROWSER_ROW_LIMIT}"
    )
    return trace.main()


if __name__ == "__main__":
    raise SystemExit(main())
