from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import LARGE_TRADE_THRESHOLD_KRW, normalized_price


_AGGREGATE_KEYS = (
    "collector_buy_qty",
    "collector_sell_qty",
    "collector_trade_count",
    "collector_large_trade_buy_count_delta",
    "collector_large_trade_sell_count_delta",
    "collector_large_trade_buy_sum_eok_delta",
    "collector_large_trade_sell_sum_eok_delta",
)
_INTEGER_KEYS = {
    "collector_buy_qty",
    "collector_sell_qty",
    "collector_trade_count",
    "collector_large_trade_buy_count_delta",
    "collector_large_trade_sell_count_delta",
}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _event_trade_price(event: dict[str, Any]) -> int | None:
    values = event.get("values") if isinstance(event.get("values"), dict) else {}
    kwargs = event.get("kwargs") if isinstance(event.get("kwargs"), dict) else {}
    raw = (
        kwargs.get("raw")
        if isinstance(kwargs.get("raw"), dict)
        else values.get("raw")
        if isinstance(values.get("raw"), dict)
        else {}
    )
    return normalized_price(
        raw.get("price_raw")
        or kwargs.get("price")
        or values.get("price")
        or kwargs.get("trade_price")
        or values.get("trade_price")
        or kwargs.get("realtime_price")
        or values.get("realtime_price")
    )


def _merge_aggregate_kwargs(
    newer: dict[str, Any],
    older: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(older, dict):
        return newer
    older_kwargs = older.get("kwargs") if isinstance(older.get("kwargs"), dict) else {}
    if not any(older_kwargs.get(key) not in (None, 0, 0.0, "") for key in _AGGREGATE_KEYS):
        return newer

    merged = deepcopy(newer)
    newer_kwargs = dict(
        merged.get("kwargs") if isinstance(merged.get("kwargs"), dict) else {}
    )
    for key in _AGGREGATE_KEYS:
        total = _number(newer_kwargs.get(key)) + _number(older_kwargs.get(key))
        if not total:
            continue
        newer_kwargs[key] = int(total) if key in _INTEGER_KEYS else round(total, 4)
    merged["kwargs"] = newer_kwargs
    return merged


def install(base) -> None:
    """Aggregate every >= 50 million KRW signed tick before latest-only coalescing.

    This patch is intentionally pure Python. It adds no QAx owner, timer, TR request,
    orderbook scheduler, or provider stack. The minimal collector reads signed FID 15
    once per trade callback; this patch preserves all qualifying ticks while the normal
    display quote remains latest-only per symbol.
    """

    sender_class = base.EventSender
    if getattr(sender_class, "_stockboard_large_trade_aggregate_installed", False):
        return

    original_init = sender_class.__init__
    original_publish_trade = sender_class.publish_trade
    original_attach_trade_flow = sender_class._attach_trade_flow
    original_stats = sender_class.stats

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.large_trade_flow_by_code: dict[str, dict[str, float]] = {}
        self.large_trade_buy_count = 0
        self.large_trade_sell_count = 0
        self.large_trade_buy_sum_eok = 0.0
        self.large_trade_sell_sum_eok = 0.0

    def record_large_trade(self, event: dict[str, Any]) -> None:
        code = base.normalize_code(event.get("stock_code"))
        qty = base._event_trade_qty(event)
        price = _event_trade_price(event)
        if not code or not qty or not price:
            return

        trade_amount_krw = abs(int(qty)) * int(price)
        if trade_amount_krw < LARGE_TRADE_THRESHOLD_KRW:
            return

        eok = trade_amount_krw / 100_000_000
        with self.lock:
            flow = self.large_trade_flow_by_code.setdefault(
                code,
                {
                    "buy_count": 0,
                    "sell_count": 0,
                    "buy_sum_eok": 0.0,
                    "sell_sum_eok": 0.0,
                },
            )
            if qty > 0:
                flow["buy_count"] += 1
                flow["buy_sum_eok"] += eok
                self.large_trade_buy_count += 1
                self.large_trade_buy_sum_eok += eok
            else:
                flow["sell_count"] += 1
                flow["sell_sum_eok"] += eok
                self.large_trade_sell_count += 1
                self.large_trade_sell_sum_eok += eok

    def publish_trade(self, event: dict[str, Any]) -> None:
        code = base.normalize_code(event.get("stock_code"))
        with self.lock:
            older_pending = self.latest_trade_by_code.get(code) if code else None
        next_event = _merge_aggregate_kwargs(event, older_pending)
        record_large_trade(self, next_event)
        return original_publish_trade(self, next_event)

    def attach_large_trade_flow(
        self,
        code: str,
        event: dict[str, Any],
        trade_flow: dict[str, int],
        large_flow: dict[str, float],
    ) -> dict[str, Any]:
        existing_kwargs = dict(
            event.get("kwargs") if isinstance(event.get("kwargs"), dict) else {}
        )
        next_event = (
            original_attach_trade_flow(self, code, event, trade_flow)
            if trade_flow
            else event
        )
        next_event = dict(next_event)
        kwargs = dict(
            next_event.get("kwargs")
            if isinstance(next_event.get("kwargs"), dict)
            else {}
        )

        # Requeued aggregate values can already be attached to the latest event.
        # Add the current micro-batch instead of replacing those unsent values.
        for key in ("collector_buy_qty", "collector_sell_qty", "collector_trade_count"):
            previous = _number(existing_kwargs.get(key))
            current = _number(kwargs.get(key))
            total = previous + current
            if total:
                kwargs[key] = int(total)

        large_values = {
            "collector_large_trade_buy_count_delta": _number(
                existing_kwargs.get("collector_large_trade_buy_count_delta")
            )
            + _number(large_flow.get("buy_count")),
            "collector_large_trade_sell_count_delta": _number(
                existing_kwargs.get("collector_large_trade_sell_count_delta")
            )
            + _number(large_flow.get("sell_count")),
            "collector_large_trade_buy_sum_eok_delta": _number(
                existing_kwargs.get("collector_large_trade_buy_sum_eok_delta")
            )
            + _number(large_flow.get("buy_sum_eok")),
            "collector_large_trade_sell_sum_eok_delta": _number(
                existing_kwargs.get("collector_large_trade_sell_sum_eok_delta")
            )
            + _number(large_flow.get("sell_sum_eok")),
        }
        for key, total in large_values.items():
            if not total:
                continue
            kwargs[key] = int(total) if key in _INTEGER_KEYS else round(total, 4)

        if large_flow or any(
            kwargs.get(key) not in (None, 0, 0.0, "")
            for key in (
                "collector_large_trade_buy_count_delta",
                "collector_large_trade_sell_count_delta",
                "collector_large_trade_buy_sum_eok_delta",
                "collector_large_trade_sell_sum_eok_delta",
            )
        ):
            kwargs["collector_large_trade_window_ms"] = int(self.flush_sec * 1000)
            kwargs["large_trade_threshold_krw"] = LARGE_TRADE_THRESHOLD_KRW

        next_event["kwargs"] = kwargs
        return next_event

    def drain(self) -> list[dict[str, Any]]:
        # Copy and clear normal flow and large-trade flow under one lock so a newly
        # arriving tick can never be detached from the next outgoing quote.
        with self.lock:
            direct = list(self.direct_events)
            trade_items = list(self.latest_trade_by_code.items())
            orderbooks = list(self.latest_orderbook_by_code.values())
            flows = dict(self.trade_flow_by_code)
            large_flows = dict(self.large_trade_flow_by_code)
            self.direct_events.clear()
            self.latest_trade_by_code.clear()
            self.latest_orderbook_by_code.clear()
            self.trade_flow_by_code.clear()
            self.large_trade_flow_by_code.clear()

        trades = [
            attach_large_trade_flow(
                self,
                code,
                event,
                flows.get(code, {}),
                large_flows.get(code, {}),
            )
            for code, event in trade_items
        ]
        return [*direct, *trades, *orderbooks]

    def stats(self) -> dict[str, Any]:
        result = original_stats(self)
        with self.lock:
            result.update(
                {
                    "large_trade_aggregate_enabled": True,
                    "pending_large_trade_code_count": len(
                        self.large_trade_flow_by_code
                    ),
                    "large_trade_buy_count": int(self.large_trade_buy_count),
                    "large_trade_sell_count": int(self.large_trade_sell_count),
                    "large_trade_buy_sum_eok": round(
                        float(self.large_trade_buy_sum_eok), 4
                    ),
                    "large_trade_sell_sum_eok": round(
                        float(self.large_trade_sell_sum_eok), 4
                    ),
                    "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
                    "large_trade_policy": "signed_fid15_every_tick_latest_quote_aggregate_v1",
                    "large_trade_requeue_preserve_enabled": True,
                }
            )
        return result

    sender_class.__init__ = init
    sender_class.publish_trade = publish_trade
    sender_class._drain = drain
    sender_class.stats = stats
    sender_class._stockboard_large_trade_aggregate_installed = True
