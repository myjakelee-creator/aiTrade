from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import LARGE_TRADE_THRESHOLD_KRW, normalized_price

base = importlib.import_module("realtime_v2.collector32")


def _event_trade_price(event: dict[str, Any]) -> int | None:
    values = event.get("values") if isinstance(event.get("values"), dict) else {}
    kwargs = event.get("kwargs") if isinstance(event.get("kwargs"), dict) else {}
    raw = kwargs.get("raw") if isinstance(kwargs.get("raw"), dict) else values.get("raw") if isinstance(values.get("raw"), dict) else {}
    return normalized_price(
        raw.get("price_raw")
        or kwargs.get("price")
        or values.get("price")
        or kwargs.get("trade_price")
        or values.get("trade_price")
        or kwargs.get("realtime_price")
        or values.get("realtime_price")
    )


_original_init = base.EventSender.__init__


def _patched_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    self.large_trade_flow_by_code: dict[str, dict[str, float]] = {}
    self.large_trade_buy_count = 0
    self.large_trade_sell_count = 0
    self.large_trade_buy_sum_eok = 0.0
    self.large_trade_sell_sum_eok = 0.0


base.EventSender.__init__ = _patched_init


def _record_large_trade(self, event: dict[str, Any]) -> None:
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
        large = self.large_trade_flow_by_code.setdefault(
            code,
            {
                "buy_count": 0,
                "sell_count": 0,
                "buy_sum_eok": 0.0,
                "sell_sum_eok": 0.0,
            },
        )
        if qty > 0:
            large["buy_count"] += 1
            large["buy_sum_eok"] += eok
            self.large_trade_buy_count += 1
            self.large_trade_buy_sum_eok += eok
        elif qty < 0:
            large["sell_count"] += 1
            large["sell_sum_eok"] += eok
            self.large_trade_sell_count += 1
            self.large_trade_sell_sum_eok += eok


_original_publish_trade = base.EventSender.publish_trade


def _patched_publish_trade(self, event: dict[str, Any]) -> None:
    _record_large_trade(self, event)
    return _original_publish_trade(self, event)


base.EventSender.publish_trade = _patched_publish_trade


_original_attach_trade_flow = base.EventSender._attach_trade_flow


def _attach_large_trade_flow(
    self,
    code: str,
    event: dict[str, Any],
    flow: dict[str, int],
    large_flow: dict[str, float],
) -> dict[str, Any]:
    next_event = _original_attach_trade_flow(self, code, event, flow) if flow else event
    if not large_flow:
        return next_event

    next_event = dict(next_event)
    kwargs = dict(next_event.get("kwargs") if isinstance(next_event.get("kwargs"), dict) else {})
    kwargs["collector_large_trade_buy_count_delta"] = int(large_flow.get("buy_count") or 0)
    kwargs["collector_large_trade_sell_count_delta"] = int(large_flow.get("sell_count") or 0)
    kwargs["collector_large_trade_buy_sum_eok_delta"] = round(float(large_flow.get("buy_sum_eok") or 0.0), 4)
    kwargs["collector_large_trade_sell_sum_eok_delta"] = round(float(large_flow.get("sell_sum_eok") or 0.0), 4)
    kwargs["collector_large_trade_window_ms"] = int(self.flush_sec * 1000)
    kwargs["large_trade_threshold_krw"] = LARGE_TRADE_THRESHOLD_KRW
    next_event["kwargs"] = kwargs
    return next_event


def _patched_drain(self) -> list[dict[str, Any]]:
    with self.lock:
        direct = list(self.direct_events)
        trade_items = list(self.latest_trade_by_code.items())
        orderbooks = list(self.latest_orderbook_by_code.values())
        flows = dict(self.trade_flow_by_code)
        large_flows = dict(getattr(self, "large_trade_flow_by_code", {}))
        self.direct_events.clear()
        self.latest_trade_by_code.clear()
        self.latest_orderbook_by_code.clear()
        self.trade_flow_by_code.clear()
        if hasattr(self, "large_trade_flow_by_code"):
            self.large_trade_flow_by_code.clear()
    trades = [
        _attach_large_trade_flow(self, code, event, flows.get(code, {}), large_flows.get(code, {}))
        for code, event in trade_items
    ]
    return [*direct, *trades, *orderbooks]


base.EventSender._drain = _patched_drain


_original_stats = base.EventSender.stats


def _patched_stats(self) -> dict[str, Any]:
    result = _original_stats(self)
    with self.lock:
        result.update(
            {
                "pending_large_trade_code_count": len(getattr(self, "large_trade_flow_by_code", {})),
                "large_trade_buy_count": int(getattr(self, "large_trade_buy_count", 0)),
                "large_trade_sell_count": int(getattr(self, "large_trade_sell_count", 0)),
                "large_trade_buy_sum_eok": round(float(getattr(self, "large_trade_buy_sum_eok", 0.0)), 4),
                "large_trade_sell_sum_eok": round(float(getattr(self, "large_trade_sell_sum_eok", 0.0)), 4),
                "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
            }
        )
    return result


base.EventSender.stats = _patched_stats


if __name__ == "__main__":
    raise SystemExit(base.main())
