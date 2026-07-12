from __future__ import annotations

import threading
import time
from typing import Any


_lock = threading.RLock()


def install(market_session_module: Any, *, check_interval_sec: float = 5.0) -> None:
    """Cache the market-calendar JSON and re-read it only when the file changes."""

    if getattr(market_session_module, "_stockboard_market_session_cache_installed", False):
        return

    original_load = market_session_module._load_config
    interval = max(0.25, float(check_interval_sec))
    cache: dict[str, Any] | None = None
    cached_mtime_ns: int | None = None
    last_check_mono = 0.0
    read_count = 0
    hit_count = 0

    def cached_load_config() -> dict[str, Any]:
        nonlocal cache, cached_mtime_ns, last_check_mono, read_count, hit_count

        now_mono = time.monotonic()
        with _lock:
            if cache is not None and now_mono - last_check_mono < interval:
                hit_count += 1
                market_session_module._market_session_config_cache_hits = hit_count
                return cache

            last_check_mono = now_mono
            try:
                mtime_ns = market_session_module.CONFIG_PATH.stat().st_mtime_ns
            except OSError:
                mtime_ns = None

            if cache is not None and mtime_ns == cached_mtime_ns:
                hit_count += 1
                market_session_module._market_session_config_cache_hits = hit_count
                return cache

            loaded = original_load()
            cache = loaded if isinstance(loaded, dict) else {}
            cached_mtime_ns = mtime_ns
            read_count += 1
            market_session_module._market_session_config_read_count = read_count
            market_session_module._market_session_config_cache_hits = hit_count
            market_session_module._market_session_config_cached_mtime_ns = cached_mtime_ns
            return cache

    market_session_module._load_config = cached_load_config
    market_session_module._stockboard_market_session_cache_installed = True
