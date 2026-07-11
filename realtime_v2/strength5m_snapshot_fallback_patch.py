from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "runtime" / "stockboard_v2" / "snapshot.json"

_STATE: dict[str, Any] = {
    "source": None,
    "http_error": None,
    "file_age_sec": None,
    "last_loaded_at": None,
}


def _read_snapshot_file() -> tuple[dict[str, Any], float | None]:
    try:
        stat = SNAPSHOT_PATH.stat()
        payload = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}, None
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        return {}, None
    age = max(0.0, time.time() - stat.st_mtime)
    return payload, round(age, 3)


def install() -> None:
    from realtime_v2 import strength5m_scheduler as scheduler_module

    if getattr(scheduler_module, "_stockboard_snapshot_fallback_installed", False):
        return

    timeout_sec = max(
        1.5,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_SNAPSHOT_HTTP_TIMEOUT_SEC", "5")),
    )
    fresh_file_sec = max(
        1.0,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_SNAPSHOT_FILE_FRESH_SEC", "15")),
    )
    stale_fallback_sec = max(
        fresh_file_sec,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_SNAPSHOT_FILE_STALE_FALLBACK_SEC", "300")),
    )

    def read_json_url_with_file_fallback(url: str) -> dict[str, Any]:
        payload, age = _read_snapshot_file()
        if payload and age is not None and age <= fresh_file_sec:
            _STATE.update(
                {
                    "source": "snapshot_file",
                    "http_error": None,
                    "file_age_sec": age,
                    "last_loaded_at": time.time(),
                }
            )
            return payload

        http_error: Exception | None = None
        try:
            with urlopen(url, timeout=timeout_sec) as response:
                value = json.loads(response.read().decode("utf-8"))
            if isinstance(value, dict):
                _STATE.update(
                    {
                        "source": "snapshot_http",
                        "http_error": None,
                        "file_age_sec": age,
                        "last_loaded_at": time.time(),
                    }
                )
                return value
        except Exception as error:  # keep the scheduler alive and try the file below
            http_error = error

        if payload and age is not None and age <= stale_fallback_sec:
            _STATE.update(
                {
                    "source": "snapshot_file_stale_fallback",
                    "http_error": str(http_error) if http_error else None,
                    "file_age_sec": age,
                    "last_loaded_at": time.time(),
                }
            )
            return payload

        _STATE.update(
            {
                "source": "unavailable",
                "http_error": str(http_error) if http_error else "snapshot unavailable",
                "file_age_sec": age,
                "last_loaded_at": None,
            }
        )
        if http_error is not None:
            raise http_error
        raise RuntimeError("worker snapshot unavailable")

    scheduler_module._read_json_url = read_json_url_with_file_fallback

    original_stats = scheduler_module.Strength5mScheduler.stats

    def patched_stats(self) -> dict[str, Any]:
        result = original_stats(self)
        result.update(
            {
                "snapshot_source": _STATE.get("source"),
                "snapshot_http_error": _STATE.get("http_error"),
                "snapshot_file_path": str(SNAPSHOT_PATH),
                "snapshot_file_age_sec": _STATE.get("file_age_sec"),
                "snapshot_last_loaded_epoch": _STATE.get("last_loaded_at"),
            }
        )
        return result

    scheduler_module.Strength5mScheduler.stats = patched_stats
    scheduler_module._stockboard_snapshot_fallback_installed = True
