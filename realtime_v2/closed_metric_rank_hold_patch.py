from __future__ import annotations

"""Prevent the auxiliary metric stream from changing closed-board row order.

The full Worker snapshot owns ranking and lane placement. During active sessions the
lightweight metric stream may refresh current-day trade-value ranks. After the
calendar-defined close, however, the board is a last-good/checkpoint view and the
metric stream must update auxiliary scalars only. Omitting ``rank`` preserves the
rank already carried by the last full payload in the browser.

This patch adds no collector, QAx, FID, REST, WebSocket, thread, timer, browser
calculation, or SSE cadence change.
"""

from typing import Any

PATCH_VERSION = "closed_metric_rank_hold_v1"
_CLOSED_PHASES = frozenset(
    {
        "closed",
        "before_market",
        "weekend",
        "holiday",
        "outside",
    }
)
_CLOSED_BASES = frozenset(
    {
        "in_memory_after_close_live_hold",
        "after_close_live_checkpoint",
        "portable_exact_close",
        "previous_session_final_until_premarket",
    }
)


def _closed_display(state: Any) -> bool:
    status = getattr(state, "status", {})
    if not isinstance(status, dict):
        return False
    for key in (
        "board_market_phase",
        "market_phase",
        "market_session_phase",
        "session_phase",
    ):
        phase = str(status.get(key) or "").strip().lower()
        if phase:
            return phase in _CLOSED_PHASES
    basis = str(status.get("board_display_basis") or "").strip().lower()
    return basis in _CLOSED_BASES


def strip_closed_rank(payload: dict[str, Any], state: Any) -> dict[str, Any]:
    if not _closed_display(state):
        payload["metric_rank_mode"] = "active_current_day_rank"
        return payload
    rows = payload.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict):
                row.pop("rank", None)
    payload["metric_rank_mode"] = "closed_full_snapshot_rank_hold"
    return payload


def install() -> None:
    from realtime_v2 import metric_fast_sse_patch as metric

    original = metric.build_metric_snapshot
    if getattr(original, "_stockboard_closed_metric_rank_hold_installed", False):
        return

    def build_metric_snapshot(state: Any, *, limit: int, now_text):
        payload = original(state, limit=limit, now_text=now_text)
        return strip_closed_rank(payload, state)

    build_metric_snapshot._stockboard_closed_metric_rank_hold_installed = True
    build_metric_snapshot._stockboard_closed_metric_rank_hold_version = PATCH_VERSION
    metric.build_metric_snapshot = build_metric_snapshot


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_closed_metric_rank_hold_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        install()
        state_class = getattr(base, "State", None)
        if state_class is not None:
            state_class._stockboard_closed_metric_rank_hold_installed = True
            state_class._stockboard_closed_metric_rank_hold_version = PATCH_VERSION

    opening_module.install = install_after_opening
    opening_module._closed_metric_rank_hold_install_wrapped = True
