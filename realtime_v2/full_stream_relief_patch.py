from __future__ import annotations

"""Reduce full-snapshot CPU contention while keeping displayed metrics fresh.

The opening-burst cache already moves heavy ranking/candidate work away from HTTP/SSE,
but its 500 ms default rebuild cadence can keep the background calculator continuously
busy when one build is expensive.  That contends with the full SSE serializer and makes
full-stream latency alternate between sub-second and several seconds.

This patch changes only cache policy defaults and the fields copied from current Worker
quotes onto cached rows.  It does not alter QAx, FIDs, Collector, EventSender, price SSE,
ranking formulas, metric calculations, or browser render logic.
"""

import os
from typing import Any

PATCH_VERSION = "full_stream_relief_v1"
HEAVY_INTERVAL_MS = 1500
HEAVY_MAX_AGE_MS = 4000

LIVE_OVERLAY_FIELDS = (
    "trade_value_eok",
    "trade_value_trading_date",
    "amount_ratio",
    "amount_ratio_missing_reason",
    "trade_value_1m_eok",
    "trade_value_1m_ratio",
    "trade_value_1m_status",
    "bid_ask_ratio",
    "bid_pct",
    "ask_pct",
    "orderbook_received_at",
    "execution_strength",
    "execution_strength_received_at",
    "strength_5m",
    "strength_5m_received_at",
    "program_net",
    "program_net_updated_at",
    "large_trade_net_count",
    "large_trade_net_sum_eok",
    "large_trade_buy_count",
    "large_trade_sell_count",
)


def _extend_overlay_fields(opening: Any) -> None:
    existing = tuple(getattr(opening, "_FAST_OVERLAY_FIELDS", ()) or ())
    merged = list(existing)
    for field in LIVE_OVERLAY_FIELDS:
        if field not in merged:
            merged.append(field)
    opening._FAST_OVERLAY_FIELDS = tuple(merged)


def install_runtime_wrapper() -> None:
    from realtime_v2 import worker_opening_burst_cache_patch as opening

    if getattr(opening, "_full_stream_relief_install_wrapped", False):
        return

    os.environ.setdefault("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", str(HEAVY_INTERVAL_MS))
    os.environ.setdefault("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", str(HEAVY_MAX_AGE_MS))
    _extend_overlay_fields(opening)

    original_install = opening.install

    def install_with_relief(base) -> None:
        original_install(base)
        state_class = getattr(base, "State", None)
        if state_class is None or getattr(state_class, "_stockboard_full_stream_relief_installed", False):
            return

        original_snapshot = state_class.snapshot

        def snapshot_with_relief_status(self, limit: int = 300):
            payload = original_snapshot(self, limit)
            status = payload.get("status") if isinstance(payload, dict) else None
            if isinstance(status, dict):
                status["full_stream_relief_version"] = PATCH_VERSION
                status["full_stream_relief_heavy_interval_ms"] = HEAVY_INTERVAL_MS
                status["full_stream_relief_heavy_max_age_ms"] = HEAVY_MAX_AGE_MS
                status["full_stream_relief_overlay_field_count"] = len(
                    getattr(opening, "_FAST_OVERLAY_FIELDS", ()) or ()
                )
            return payload

        state_class.snapshot = snapshot_with_relief_status
        state_class._stockboard_full_stream_relief_installed = True
        state_class._stockboard_full_stream_relief_version = PATCH_VERSION

    opening.install = install_with_relief
    opening._full_stream_relief_install_wrapped = True


__all__ = [
    "HEAVY_INTERVAL_MS",
    "HEAVY_MAX_AGE_MS",
    "LIVE_OVERLAY_FIELDS",
    "PATCH_VERSION",
    "install_runtime_wrapper",
]
