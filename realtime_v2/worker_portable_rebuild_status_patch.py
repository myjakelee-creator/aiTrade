from __future__ import annotations

"""Expose portable context rebuild progress through the Worker snapshot status."""

import json
import time
from pathlib import Path
from typing import Any

from realtime_v2.common import RUNTIME_DIR

PATCH_VERSION = "portable_rebuild_status_bridge_v1"
CONTEXT_STATUS_PATH = RUNTIME_DIR / "context_snapshot_status.json"
STATUS_FIELDS = (
    "context_process_ready",
    "context_board_ready",
    "portable_board_refresh_status",
    "portable_board_target_trading_date",
    "portable_board_market_phase",
    "portable_board_completed_count",
    "portable_board_requested_count",
    "portable_board_pending_count",
    "portable_board_refresh_count",
    "portable_board_refresh_coverage",
    "portable_board_retry_attempt",
    "portable_board_last_error",
    "portable_board_next_retry_at",
    "portable_board_candidate_path",
)

_last_check = 0.0
_last_mtime: float | None = None
_last_payload: dict[str, Any] = {}


def _read_status(path: Path | None = None) -> dict[str, Any]:
    global _last_check, _last_mtime, _last_payload
    target = Path(path or CONTEXT_STATUS_PATH)
    now = time.monotonic()
    if now - _last_check < 2.0:
        return _last_payload
    _last_check = now
    try:
        mtime = target.stat().st_mtime
    except OSError:
        _last_mtime = None
        _last_payload = {}
        return _last_payload
    if _last_mtime == mtime:
        return _last_payload
    _last_mtime = mtime
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
        _last_payload = payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        _last_payload = {}
    return _last_payload


def install() -> None:
    from realtime_v2 import worker_board_trading_date_guard as guard

    guard_class = guard.PortableBoardGuard
    if getattr(guard_class, "_portable_rebuild_status_bridge_installed", False):
        return
    original_apply = guard_class.apply

    def apply(self, state, now=None):
        result = original_apply(self, state, now)
        context_status = _read_status()
        with state.lock:
            state.status["portable_rebuild_status_bridge_version"] = PATCH_VERSION
            for field in STATUS_FIELDS:
                if field in context_status:
                    state.status[field] = context_status.get(field)
        return result

    guard_class.apply = apply
    guard_class._portable_rebuild_status_bridge_installed = True
