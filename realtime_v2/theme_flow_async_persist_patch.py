from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any


def install(flow_module) -> None:
    """Move Theme flow hold JSON writes off the projection worker thread.

    This hook runs immediately after the summary split is installed and before the
    flow-history wrapper is imported into the shared Hub. Use that exact boundary to
    preserve the inner summary-core timing and to install the process-wide market
    calendar parse cache before any projection worker starts.
    """

    if getattr(flow_module, "_stockboard_async_persist_installed", False):
        return

    from realtime_v2 import market_session as market_session_module
    from realtime_v2 import theme_projection_engine as theme_projection_module
    from realtime_v2.market_session_config_cache_patch import (
        install as install_market_session_config_cache,
    )
    from realtime_v2.theme_projection_core_timing_patch import (
        install as install_theme_projection_core_timing,
    )

    install_market_session_config_cache(market_session_module)
    install_theme_projection_core_timing(theme_projection_module)

    original_write = flow_module.atomic_write_json
    lock = threading.RLock()
    wakeup = threading.Event()
    pending: dict[str, tuple[Path, dict[str, Any]]] = {}

    def enqueue(path: Path, payload: dict[str, Any]) -> None:
        key = str(path)
        with lock:
            pending[key] = (Path(path), dict(payload))
        wakeup.set()

    def run() -> None:
        while True:
            wakeup.wait()
            wakeup.clear()
            time.sleep(0.02)
            with lock:
                jobs = list(pending.values())
                pending.clear()
            for path, payload in jobs:
                try:
                    original_write(path, payload)
                except Exception:
                    # The next completed projection will enqueue a fresh snapshot.
                    continue

    thread = threading.Thread(
        target=run,
        name="theme-flow-persist",
        daemon=True,
    )
    thread.start()
    flow_module.atomic_write_json = enqueue
    flow_module._stockboard_async_persist_installed = True
    flow_module._stockboard_async_persist_thread = thread
    flow_module._stockboard_async_persist_policy = (
        "latest_only_per_path_background_writer"
    )
