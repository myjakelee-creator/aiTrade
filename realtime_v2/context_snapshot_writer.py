from __future__ import annotations

"""Compatibility entrypoint for the shared single-flight context writer.

The implementation lives in ``context_snapshot_writer_base``. All production
launchers keep using this historical path, but every Yahoo/market-supply/OHLC
request is now routed through the cross-process TR single-flight coordinator.
"""

from realtime_v2.context_snapshot_writer_singleflight import *  # noqa: F401,F403
from realtime_v2.context_snapshot_writer_singleflight import main


if __name__ == "__main__":
    raise SystemExit(main())
