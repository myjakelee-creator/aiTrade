from __future__ import annotations

"""Normalize approved persisted UI aliases before six-metric lifecycle restore.

This patch only adapts already persisted Worker values for closed-session restore.
It adds no QAx/FID/REST/WebSocket/thread/timer/SSE work and does not change the
active price or trade-value path.
"""

from typing import Any

PATCH_VERSION = "closed_metric_alias_restore_v1"


def normalize_metric_aliases(values: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(values) if isinstance(values, dict) else {}

    aliases = {
        "bid_ask_ratio": ("ui_bid_ask_ratio", "last_valid_bid_ask_ratio"),
        "bid_pct": ("ui_bid_pct", "last_valid_bid_pct"),
        "ask_pct": ("ui_ask_pct", "last_valid_ask_pct"),
        "bid_volume": ("ui_bid_volume", "last_valid_bid_volume"),
        "ask_volume": ("ui_ask_volume", "last_valid_ask_volume"),
        "best_ask_price": ("ui_best_ask_price",),
        "best_bid_price": ("ui_best_bid_price",),
        "orderbook_received_at": (
            "ui_orderbook_observed_at",
            "last_valid_orderbook_at",
        ),
        "execution_strength": (
            "ui_execution_strength",
            "last_valid_execution_strength",
        ),
        "execution_strength_received_at": (
            "ui_execution_strength_observed_at",
            "last_valid_strength_at",
        ),
        "strength_5m": ("ui_strength_5m", "last_valid_strength_5m"),
        "strength_20m": ("ui_strength_20m",),
        "strength_60m": ("ui_strength_60m",),
        "strength_snapshot_at": (
            "ui_strength_observed_at",
            "last_valid_strength_at",
        ),
    }

    for target, sources in aliases.items():
        if result.get(target) not in (None, ""):
            continue
        for source in sources:
            value = result.get(source)
            if value not in (None, ""):
                result[target] = value
                break

    orderbook_date = result.get("orderbook_source_trading_date") or result.get(
        "ui_orderbook_source_trading_date"
    )
    if orderbook_date not in (None, ""):
        result.setdefault("orderbook_source_trading_date", orderbook_date)
        result.setdefault("_session_hold_orderbook_date", orderbook_date)

    execution_date = result.get("execution_source_trading_date") or result.get(
        "ui_execution_source_trading_date"
    )
    if execution_date not in (None, ""):
        result.setdefault("execution_source_trading_date", execution_date)
        result.setdefault("_session_hold_execution_date", execution_date)

    strength_date = result.get("strength_source_trading_date") or result.get(
        "ui_strength_source_trading_date"
    )
    if strength_date not in (None, ""):
        result.setdefault("strength_source_trading_date", strength_date)
        result.setdefault("_session_hold_strength5_date", strength_date)

    return result


def install() -> None:
    from realtime_v2 import worker_six_metric_lifecycle_patch as lifecycle

    if getattr(lifecycle, "_closed_metric_alias_restore_installed", False):
        return

    original_prepare_amount_ratio = lifecycle._prepare_amount_ratio
    original_group_usable = lifecycle._group_usable
    original_capture_values = lifecycle._capture_values
    original_group_date = lifecycle._group_date

    def prepare_amount_ratio(values):
        return original_prepare_amount_ratio(normalize_metric_aliases(values))

    def group_usable(values, group):
        return original_group_usable(normalize_metric_aliases(values), group)

    def capture_values(values, group):
        return original_capture_values(normalize_metric_aliases(values), group)

    def group_date(values, group, fallback=""):
        return original_group_date(normalize_metric_aliases(values), group, fallback)

    lifecycle._prepare_amount_ratio = prepare_amount_ratio
    lifecycle._group_usable = group_usable
    lifecycle._capture_values = capture_values
    lifecycle._group_date = group_date
    lifecycle._closed_metric_alias_restore_installed = True
    lifecycle._closed_metric_alias_restore_version = PATCH_VERSION
