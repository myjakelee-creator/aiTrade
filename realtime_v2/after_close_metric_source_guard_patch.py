from __future__ import annotations

"""Sanitize execution-strength provenance only in after-close checkpoints.

This patch does not touch live trade application, Collector, REST/WebSocket cadence,
or browser calculation. It only prevents an untrusted/legacy execution-strength
alias from being persisted or restored through the local after-close checkpoint.
Five-minute strength remains in its independent ka10046 lane.
"""

from copy import deepcopy
from typing import Any

from realtime_v2.execution_strength_alias_patch import sanitize_execution_values

PATCH_VERSION = "after_close_metric_source_guard_v1"


def sanitize_checkpoint_quote(values: dict[str, Any] | None) -> dict[str, Any]:
    return sanitize_execution_values(values)


def install_runtime_wrapper() -> None:
    from realtime_v2 import after_close_live_hold_patch as close_hold

    if getattr(close_hold, "_after_close_metric_source_guard_installed", False):
        return

    original_checkpoint_rows = close_hold._checkpoint_rows
    original_read_checkpoint = close_hold._read_checkpoint

    def checkpoint_rows(state, target_date: str):
        rows = original_checkpoint_rows(state, target_date)
        return {
            str(code): sanitize_checkpoint_quote(quote)
            for code, quote in rows.items()
            if isinstance(quote, dict)
        }

    def read_checkpoint(path):
        payload = original_read_checkpoint(path)
        if not isinstance(payload, dict):
            return payload
        rows = payload.get("rows")
        if not isinstance(rows, dict):
            return payload
        sanitized = deepcopy(payload)
        sanitized["rows"] = {
            str(code): sanitize_checkpoint_quote(quote)
            for code, quote in rows.items()
            if isinstance(quote, dict)
        }
        sanitized["metric_source_guard_version"] = PATCH_VERSION
        return sanitized

    close_hold._checkpoint_rows = checkpoint_rows
    close_hold._read_checkpoint = read_checkpoint
    close_hold._after_close_metric_source_guard_installed = True
    close_hold._after_close_metric_source_guard_version = PATCH_VERSION
