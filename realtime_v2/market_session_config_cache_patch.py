from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


def install(market_session_module) -> None:
    """Cache parsed market-calendar JSON until the file signature changes.

    ``market_session_now`` is called from several one-second projection wrappers. The
    original implementation reparsed the same calendar JSON on every call. This patch
    still checks the file signature every time, so edits are detected immediately, but
    reuses the already-parsed object while path, mtime and size are unchanged.
    """

    if getattr(market_session_module, "_stockboard_calendar_config_cache_installed", False):
        return

    original_load = market_session_module._load_config
    lock = threading.RLock()
    cached_signature: tuple[str, int, int] | None = None
    cached_payload: dict[str, Any] = {}
    hit_count = 0
    miss_count = 0

    def load_config() -> dict[str, Any]:
        nonlocal cached_signature, cached_payload, hit_count, miss_count

        path = Path(market_session_module.CONFIG_PATH)
        try:
            stat = path.stat()
            signature = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            signature = (str(path), -1, -1)

        with lock:
            if cached_signature == signature:
                hit_count += 1
                return cached_payload

        payload = original_load()
        payload = payload if isinstance(payload, dict) else {}
        with lock:
            cached_signature = signature
            cached_payload = payload
            miss_count += 1
            market_session_module._stockboard_calendar_config_cache_status = {
                "enabled": True,
                "path": str(path),
                "signature": signature,
                "hit_count": hit_count,
                "miss_count": miss_count,
                "policy": "stat_every_call_parse_on_signature_change",
            }
            return cached_payload

    market_session_module._load_config = load_config
    market_session_module._stockboard_calendar_config_cache_installed = True
    market_session_module._stockboard_calendar_config_cache_status = {
        "enabled": True,
        "path": str(market_session_module.CONFIG_PATH),
        "signature": None,
        "hit_count": 0,
        "miss_count": 0,
        "policy": "stat_every_call_parse_on_signature_change",
    }
