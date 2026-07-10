from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from threading import RLock
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import (
    LARGE_TRADE_THRESHOLD_KRW,
    RUNTIME_DIR,
    atomic_write_json,
    normalized_price,
    now_text,
    to_number,
    trading_date_text,
)
from realtime_v2.strength5m_scheduler import install as install_strength5m_scheduler

base = importlib.import_module("realtime_v2.collector32")

_STRENGTH_SNAPSHOT_PATH = RUNTIME_DIR / "strength_snapshot.json"
_STRENGTH_SNAPSHOT_LOCK = RLock()
_STRENGTH_VALUE_KEYS = ("strength_5m", "strength_20m", "strength_60m")


def _positive_strength(value: Any) -> float | None:
    number = to_number(value)
    if number is None or float(number) <= 0:
        return None
    return round(float(number), 4)


def _load_strength_snapshot_values(today: str) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(_STRENGTH_SNAPSHOT_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    payload_date = "".join(
        character for character in str(payload.get("trading_date") or "") if character.isdigit()
    )[:8]
    if payload_date and payload_date != today:
        return {}
    raw_values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(raw_values, dict):
        return {}
    values: dict[str, dict[str, Any]] = {}
    for raw_code, raw_entry in raw_values.items():
        code = base.normalize_code(raw_code)
        if not code or not isinstance(raw_entry, dict):
            continue
        value = _positive_strength(raw_entry.get("strength_5m"))
        entry_date = "".join(
            character
            for character in str(raw_entry.get("trading_date") or payload_date or today)
            if character.isdigit()
        )[:8]
        if value is None or entry_date != today:
            continue
        entry = dict(raw_entry)
        entry["strength_5m"] = value
        values[code] = entry
    return values


def _persist_last_valid_strength(code: str, metrics: dict[str, Any]) -> None:
    value = _positive_strength(metrics.get("strength_5m"))
    code = base.normalize_code(code)
    if not code or value is None:
        return
    today = trading_date_text()
    snapshot_at = metrics.get("strength_snapshot_at") or metrics.get("strength_completed_at") or now_text()
    entry = {
        "stock_code": code,
        "trading_date": today,
        "strength_5m": value,
        "strength_source": metrics.get("strength_source") or "opt10046_probe",
        "strength_snapshot_at": snapshot_at,
        "strength_completed_at": metrics.get("strength_completed_at") or snapshot_at,
        "strength_status": metrics.get("strength_status") or "ok",
        "updated_at": now_text(),
    }
    for key in ("strength_20m", "strength_60m"):
        number = _positive_strength(metrics.get(key))
        if number is not None:
            entry[key] = number

    with _STRENGTH_SNAPSHOT_LOCK:
        values = _load_strength_snapshot_values(today)
        values[code] = entry
        atomic_write_json(
            _STRENGTH_SNAPSHOT_PATH,
            {
                "schema_version": 1,
                "source": "collector_opt10046_last_valid",
                "trading_date": today,
                "ts": now_text(),
                "values": dict(sorted(values.items())),
            },
        )


def _sanitize_close_metrics(metrics: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    next_metrics = dict(metrics)
    if "strength_5m" not in next_metrics:
        return next_metrics, False
    value = _positive_strength(next_metrics.get("strength_5m"))
    if value is None:
        # Never let zero/blank/error responses erase the current day's last valid value.
        for key in _STRENGTH_VALUE_KEYS:
            next_metrics.pop(key, None)
        next_metrics.setdefault("strength_status", "held_last_valid")
        return next_metrics, False
    next_metrics["strength_5m"] = value
    for key in ("strength_20m", "strength_60m"):
        number = _positive_strength(next_metrics.get(key))
        if number is None:
            next_metrics.pop(key, None)
        else:
            next_metrics[key] = number
    next_metrics["trading_date"] = trading_date_text()
    return next_metrics, True


_original_update_close_metrics = base.PublishingStore.update_close_metrics


def _patched_update_close_metrics(self, stock_code, metrics):
    raw_metrics = metrics if isinstance(metrics, dict) else {}
    next_metrics, has_valid_strength = _sanitize_close_metrics(raw_metrics)
    if has_valid_strength:
        _persist_last_valid_strength(stock_code, next_metrics)
    return _original_update_close_metrics(self, stock_code, next_metrics)


base.PublishingStore.update_close_metrics = _patched_update_close_metrics


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
    kwargs = dict(
        next_event.get("kwargs") if isinstance(next_event.get("kwargs"), dict) else {}
    )
    kwargs["collector_large_trade_buy_count_delta"] = int(
        large_flow.get("buy_count") or 0
    )
    kwargs["collector_large_trade_sell_count_delta"] = int(
        large_flow.get("sell_count") or 0
    )
    kwargs["collector_large_trade_buy_sum_eok_delta"] = round(
        float(large_flow.get("buy_sum_eok") or 0.0), 4
    )
    kwargs["collector_large_trade_sell_sum_eok_delta"] = round(
        float(large_flow.get("sell_sum_eok") or 0.0), 4
    )
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
        _attach_large_trade_flow(
            self,
            code,
            event,
            flows.get(code, {}),
            large_flows.get(code, {}),
        )
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
                "pending_large_trade_code_count": len(
                    getattr(self, "large_trade_flow_by_code", {})
                ),
                "large_trade_buy_count": int(
                    getattr(self, "large_trade_buy_count", 0)
                ),
                "large_trade_sell_count": int(
                    getattr(self, "large_trade_sell_count", 0)
                ),
                "large_trade_buy_sum_eok": round(
                    float(getattr(self, "large_trade_buy_sum_eok", 0.0)), 4
                ),
                "large_trade_sell_sum_eok": round(
                    float(getattr(self, "large_trade_sell_sum_eok", 0.0)), 4
                ),
                "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
            }
        )
    return result


base.EventSender.stats = _patched_stats
install_strength5m_scheduler(base)


if __name__ == "__main__":
    raise SystemExit(base.main())
