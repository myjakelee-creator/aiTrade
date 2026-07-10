from __future__ import annotations

import importlib

large = importlib.import_module("realtime_v2.collector32_large")
base = large.base

from realtime_v2.orderbook_thin_scheduler import install as install_orderbook_thin_scheduler

install_orderbook_thin_scheduler(base)

if __name__ == "__main__":
    raise SystemExit(base.main())
