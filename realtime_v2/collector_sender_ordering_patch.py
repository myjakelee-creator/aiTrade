from __future__ import annotations

import os
import time
from copy import deepcopy
from typing import Any

_AGGREGATE_KEYS = (
    "collector_buy_qty",
    "collector_sell_qty",
    "collector_trade_count",
    "collector_large_trade_buy_count_delta",
    "collector_large_trade_sell_count_delta",
    "collector_large_trade_buy_sum_eok_delta",
    "collector_large_trade_sell_sum_eok_delta",
)


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _merge_trade_aggregates(newer: dict[str, Any], older: dict[str, Any]) -> dict[str, Any]:
    """Keep the newer quote while preserving flow accumulated in the unsent quote."""

    merged = deepcopy(newer)
    newer_kwargs = dict(merged.get("kwargs") if isinstance(merged.get("kwargs"), dict) else {})
    older_kwargs = older.get("kwargs") if isinstance(older.get("kwargs"), dict) else {}

    for key in _AGGREGATE_KEYS:
        total = _number(newer_kwargs.get(key)) + _number(older_kwargs.get(key))
        if not total:
            continue
        if key.endswith("_count") or key.endswith("_qty") or key.endswith("_count_delta"):
            newer_kwargs[key] = int(total)
        else:
            newer_kwargs[key] = round(total, 4)

    merged["kwargs"] = newer_kwargs
    return merged


def install(base) -> None:
    """Preserve latest-only ordering across collector socket reconnects.

    The original requeue loop iterated forwards while using appendleft for direct
    events, which reverses collector-status heartbeats. It also allowed an older
    unsent trade to overwrite a newer trade that arrived while sendall was failing.
    Both effects make counters/timestamps move backwards and feed stale quotes back
    into the worker. Requeue direct events in original order and never replace a
    newer pending quote with an older unsent quote.
    """

    sender_class = base.EventSender
    if getattr(sender_class, "_stockboard_sender_ordering_installed", False):
        return

    original_init = sender_class.__init__
    original_stats = sender_class.stats
    original_publish_status = base.publish_collector_status

    instance_id = f"{os.getpid()}-{time.time_ns()}"
    process_started_at = base.now_text()
    status_seq = 0

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.requeue_attempt_count = 0
        self.requeue_direct_count = 0
        self.requeue_superseded_trade_count = 0
        self.requeue_superseded_orderbook_count = 0

    def requeue_unsent(self, events: list[dict[str, Any]]) -> None:
        self.requeue_attempt_count += 1
        with self.lock:
            # appendleft reverses input, so iterate backwards to preserve the
            # original direct-event order ahead of already queued newer events.
            for event in reversed(events):
                event_type = event.get("type") if isinstance(event, dict) else None
                code = base.normalize_code(event.get("stock_code")) if isinstance(event, dict) else None

                if event_type == "trade" and code:
                    newer = self.latest_trade_by_code.get(code)
                    if newer is None:
                        self.latest_trade_by_code[code] = event
                    else:
                        self.latest_trade_by_code[code] = _merge_trade_aggregates(newer, event)
                        self.requeue_superseded_trade_count += 1
                    continue

                if event_type == "orderbook" and code:
                    if code not in self.latest_orderbook_by_code:
                        self.latest_orderbook_by_code[code] = event
                    else:
                        self.requeue_superseded_orderbook_count += 1
                    continue

                self.direct_events.appendleft(event)
                self.requeue_direct_count += 1

    def stats(self) -> dict[str, Any]:
        result = original_stats(self)
        result.update(
            {
                "requeue_attempt_count": int(getattr(self, "requeue_attempt_count", 0) or 0),
                "requeue_direct_count": int(getattr(self, "requeue_direct_count", 0) or 0),
                "requeue_superseded_trade_count": int(
                    getattr(self, "requeue_superseded_trade_count", 0) or 0
                ),
                "requeue_superseded_orderbook_count": int(
                    getattr(self, "requeue_superseded_orderbook_count", 0) or 0
                ),
                "sender_ordering_policy": "latest_quote_monotonic_requeue_v1",
            }
        )
        return result

    def publish_collector_status(sender, provider, extra=None) -> None:
        nonlocal status_seq
        status_seq += 1
        next_extra = dict(extra or {})
        next_extra.update(
            {
                "collector_pid": os.getpid(),
                "collector_instance_id": instance_id,
                "collector_process_started_at": process_started_at,
                "collector_status_seq": status_seq,
            }
        )
        original_publish_status(sender, provider, next_extra)

    sender_class.__init__ = init
    sender_class._requeue_unsent = requeue_unsent
    sender_class.stats = stats
    base.publish_collector_status = publish_collector_status
    sender_class._stockboard_sender_ordering_installed = True
