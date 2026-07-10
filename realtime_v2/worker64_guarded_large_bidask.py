from __future__ import annotations

import importlib

large = importlib.import_module("realtime_v2.worker64_guarded_large")
base = large.base

from realtime_v2.bidask_last_cache_patch import install as install_bidask_last_cache

install_bidask_last_cache(base)

if __name__ == "__main__":
    raise SystemExit(base.main())
