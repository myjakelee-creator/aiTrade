import csv
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from stockboard_engine import _stock_code


DOCS_DIR = Path(__file__).resolve().parent / "docs"

QUOTE_FIELDS = (
    "price",
    "change_rate",
    "trade_price",
    "trade_qty",
    "trade_side",
    "trade_time",
    "execution_strength",
    "best_bid",
    "best_ask",
    "bid_volume",
    "ask_volume",
    "bid_volume_snapshot",
    "ask_volume_snapshot",
    "bid_pct",
    "ask_pct",
    "bid_ask_ratio_snapshot",
    "orderbook_snapshot_at",
    "orderbook_stale_sec",
    "orderbook_status",
    "orderbook_error",
    "orderbook_status_detail",
    "orderbook_requested_at",
    "orderbook_completed_at",
    "orderbook_tr_repeat_count",
    "orderbook_raw_sample",
    "orderbook_rqname",
    "orderbook_trcode",
    "orderbook_screen_no",
    "total_bid_qty",
    "total_ask_qty",
    "bid_ask_ratio",
    "orderbook_received_at",
    "orderbook_age_sec",
    "orderbook_source",
    "realtime_strength_snapshot",
    "session_buy_qty_live",
    "session_sell_qty_live",
    "session_strength",
    "session_strength_source",
    "strength_5m",
    "strength_20m",
    "strength_60m",
    "strength_snapshot_at",
    "strength_source",
    "strength_stale_sec",
    "strength_status",
    "strength_error",
    "strength_status_detail",
    "strength_requested_at",
    "strength_completed_at",
    "strength_tr_repeat_count",
    "strength_raw_sample",
    "strength_rqname",
    "strength_trcode",
    "strength_screen_no",
    "foreign_line_raw",
    "cumulative_volume",
    "cumulative_value",
    "received_code",
    "normalized_code",
    "registered_code",
    "original_registered_code",
    "realtime_source_code",
    "source_code",
    "realtime_ohlc",
    "realtime_ohlc_source",
)


class RealtimeStore:
    def __init__(self, trade_event_limit=200, orderbook_event_limit=200):
        trade_event_limit = int(trade_event_limit)
        orderbook_event_limit = int(orderbook_event_limit)
        if trade_event_limit <= 0 or orderbook_event_limit <= 0:
            raise ValueError("event limits must be positive integers")
        self._lock = RLock()
        self._quotes = {}
        self._trade_events = {}
        self._orderbook_events = {}
        self._close_metric_snapshots = {}
        self._base_ohlc = {}
        self._last_seen = {}
        self._trade_event_limit = trade_event_limit
        self._orderbook_event_limit = orderbook_event_limit
        self._sequence = 0
        self._updated_at = None

    @staticmethod
    def _normalized_code(stock_code):
        code = _stock_code(stock_code)
        if code is None:
            raise ValueError(f"invalid stock code: {stock_code!r}")
        return code

    @staticmethod
    def _timestamp_text(timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    def _ensure_quote(self, stock_code):
        quote = self._quotes.get(stock_code)
        if quote is None:
            quote = dict.fromkeys(QUOTE_FIELDS)
            quote.update(
                {
                    "stock_code": stock_code,
                    "received_at": None,
                    "updated_at": None,
                    "sequence": 0,
                    "stale": False,
                }
            )
            self._quotes[stock_code] = quote
        return quote

    def _apply_update(self, stock_code, values):
        timestamp = time.time()
        timestamp_text = self._timestamp_text(timestamp)
        self._sequence += 1
        self._updated_at = timestamp_text
        self._last_seen[stock_code] = timestamp
        quote = self._ensure_quote(stock_code)
        quote.update({key: value for key, value in values.items() if value is not None})
        quote.update(
            {
                "received_at": timestamp_text,
                "updated_at": timestamp_text,
                "sequence": self._sequence,
                "stale": False,
            }
        )
        return quote, timestamp_text, self._sequence

    @staticmethod
    def _event(stock_code, values, timestamp_text, sequence):
        event = {
            "stock_code": stock_code,
            "received_at": timestamp_text,
            "sequence": sequence,
        }
        event.update({key: value for key, value in values.items() if value is not None})
        return event

    @staticmethod
    def _orderbook_ratio(bid_volume, ask_volume):
        try:
            bid_number = float(bid_volume)
            ask_number = float(ask_volume)
        except (TypeError, ValueError):
            return None
        if bid_number < 0 or ask_number <= 0:
            return None
        return round(bid_number / ask_number, 4)

    @staticmethod
    def _session_strength(buy_qty, sell_qty):
        try:
            buy_number = float(buy_qty)
            sell_number = float(sell_qty)
        except (TypeError, ValueError):
            return None
        if buy_number <= 0 and sell_number <= 0:
            return None
        if sell_number <= 0:
            return None
        if buy_number < 0:
            return None
        return round((buy_number / sell_number) * 100, 4)

    @staticmethod
    def _snapshot_ratio(bid_volume, ask_volume):
        try:
            bid_number = float(bid_volume)
            ask_number = float(ask_volume)
        except (TypeError, ValueError):
            return None
        if bid_number < 0 or ask_number <= 0:
            return None
        return round(bid_number / ask_number, 4)

    @staticmethod
    def _bid_ask_pct(bid_volume, ask_volume):
        try:
            bid_number = float(bid_volume)
            ask_number = float(ask_volume)
        except (TypeError, ValueError):
            return None, None
        total = bid_number + ask_number
        if bid_number < 0 or ask_number < 0 or total <= 0:
            return None, None
        bid_pct = round((bid_number / total) * 100)
        return bid_pct, 100 - bid_pct

    @staticmethod
    def _price_number(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number < 0:
            number = abs(number)
        return number

    @classmethod
    def _ohlc_price(cls, ohlc, key):
        if not isinstance(ohlc, dict):
            return None
        return cls._price_number(ohlc.get(key))

    def set_base_ohlc(self, stock_code, ohlc):
        code = self._normalized_code(stock_code)
        if ohlc is None:
            return None
        if not isinstance(ohlc, dict):
            raise TypeError("ohlc must be a dict")
        with self._lock:
            self._base_ohlc[code] = deepcopy(ohlc)
            return deepcopy(self._base_ohlc[code])

    def set_base_ohlc_many(self, rows):
        count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            stock_code = row.get("stock_code")
            ohlc = row.get("ohlc")
            if stock_code and ohlc is not None:
                self.set_base_ohlc(stock_code, ohlc)
                count += 1
        return count

    def _realtime_ohlc(self, code, tick_price):
        price = self._price_number(tick_price)
        if price is None:
            return None
        base_ohlc = self._base_ohlc.get(code)
        if not isinstance(base_ohlc, dict):
            return None
        previous = self._quotes.get(code, {}).get("realtime_ohlc")
        if not isinstance(previous, dict):
            previous = {}

        base_open = self._ohlc_price(base_ohlc, "open")
        high_candidates = [
            self._ohlc_price(base_ohlc, "high"),
            self._ohlc_price(previous, "high"),
            price,
        ]
        low_candidates = [
            self._ohlc_price(base_ohlc, "low"),
            self._ohlc_price(previous, "low"),
            price,
        ]
        high_values = [value for value in high_candidates if value is not None]
        low_values = [value for value in low_candidates if value is not None]

        realtime_ohlc = {
            "open": base_open,
            "high": max(high_values) if high_values else price,
            "low": min(low_values) if low_values else price,
            "close": price,
            "vwap": base_ohlc.get("vwap"),
            "vwap_source": "base",
            "prev_high": base_ohlc.get("prev_high"),
            "prev_close": base_ohlc.get("prev_close"),
            "prev_low": base_ohlc.get("prev_low"),
        }
        if "trading_date" in base_ohlc:
            realtime_ohlc["trading_date"] = base_ohlc.get("trading_date")
        return realtime_ohlc

    def update_trade(
        self,
        stock_code,
        *,
        price=None,
        change_rate=None,
        trade_price=None,
        trade_qty=None,
        trade_side=None,
        trade_time=None,
        execution_strength=None,
        cumulative_volume=None,
        cumulative_value=None,
        received_code=None,
        normalized_code=None,
        registered_code=None,
        original_registered_code=None,
        realtime_source_code=None,
        source_code=None,
        price_first=False,
        strength_5m=None,
        strength_20m=None,
        strength_60m=None,
        strength_snapshot_at=None,
        strength_source=None,
        strength_stale_sec=None,
    ):
        code = self._normalized_code(stock_code)
        values = {
            "price": price,
            "change_rate": change_rate,
            "trade_price": trade_price,
            "trade_qty": trade_qty,
            "trade_side": trade_side,
            "trade_time": trade_time,
            "execution_strength": execution_strength,
            "cumulative_volume": cumulative_volume,
            "cumulative_value": cumulative_value,
            "received_code": received_code,
            "normalized_code": normalized_code,
            "registered_code": registered_code,
            "original_registered_code": original_registered_code,
            "realtime_source_code": realtime_source_code,
            "source_code": source_code,
            "strength_5m": strength_5m,
            "strength_20m": strength_20m,
            "strength_60m": strength_60m,
            "strength_snapshot_at": strength_snapshot_at,
            "strength_source": strength_source,
            "strength_stale_sec": strength_stale_sec,
        }
        if price_first:
            first_values = {
                key: values.get(key)
                for key in (
                    "price",
                    "change_rate",
                    "trade_qty",
                    "trade_time",
                    "cumulative_volume",
                    "cumulative_value",
                    "received_code",
                    "normalized_code",
                    "registered_code",
                    "original_registered_code",
                    "realtime_source_code",
                    "source_code",
                )
            }
            with self._lock:
                self._ensure_quote(code)
                self._apply_update(code, first_values)
        with self._lock:
            quote = self._ensure_quote(code)
            realtime_ohlc = self._realtime_ohlc(code, price)
            if realtime_ohlc is not None:
                values.update(
                    {
                        "realtime_ohlc": realtime_ohlc,
                        "realtime_ohlc_source": (
                            "ka10086_base_plus_realtime_tick"
                        ),
                    }
                )
            session_buy_qty_live = quote.get("session_buy_qty_live") or 0
            session_sell_qty_live = quote.get("session_sell_qty_live") or 0
            try:
                trade_qty_number = float(trade_qty)
            except (TypeError, ValueError):
                trade_qty_number = 0
            if trade_qty_number > 0:
                session_buy_qty_live += trade_qty_number
            elif trade_qty_number < 0:
                session_sell_qty_live += abs(trade_qty_number)
            values.update(
                {
                    "session_buy_qty_live": session_buy_qty_live,
                    "session_sell_qty_live": session_sell_qty_live,
                    "session_strength": self._session_strength(
                        session_buy_qty_live, session_sell_qty_live
                    ),
                    "session_strength_source": "live_since_server_start",
                }
            )
            quote, timestamp_text, sequence = self._apply_update(code, values)
            events = self._trade_events.setdefault(
                code, deque(maxlen=self._trade_event_limit)
            )
            events.append(self._event(code, values, timestamp_text, sequence))
            return deepcopy(quote)

    def update_orderbook(
        self,
        stock_code,
        orderbook=None,
        *,
        best_bid=None,
        best_ask=None,
        total_bid_qty=None,
        total_ask_qty=None,
        bid_ask_ratio=None,
        received_code=None,
        normalized_code=None,
        registered_code=None,
        original_registered_code=None,
        realtime_source_code=None,
        source_code=None,
        orderbook_source=None,
    ):
        code = self._normalized_code(stock_code)
        if orderbook is not None:
            if not isinstance(orderbook, dict):
                raise TypeError("orderbook must be a dict")
            best_bid = orderbook.get("best_bid_price", best_bid)
            best_ask = orderbook.get("best_ask_price", best_ask)
            total_bid_qty = orderbook.get("bid_volume", total_bid_qty)
            total_ask_qty = orderbook.get("ask_volume", total_ask_qty)
        bid_ask_ratio = self._orderbook_ratio(total_bid_qty, total_ask_qty)
        values = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "bid_volume": total_bid_qty,
            "ask_volume": total_ask_qty,
            "total_bid_qty": total_bid_qty,
            "total_ask_qty": total_ask_qty,
            "bid_ask_ratio": bid_ask_ratio,
            "orderbook_source": orderbook_source,
            "received_code": received_code,
            "normalized_code": normalized_code,
            "registered_code": registered_code,
            "original_registered_code": original_registered_code,
            "realtime_source_code": realtime_source_code,
            "source_code": source_code,
        }
        if orderbook is not None:
            values["raw"] = orderbook.get("raw")
        with self._lock:
            quote, timestamp_text, sequence = self._apply_update(code, values)
            quote["bid_volume"] = total_bid_qty
            quote["ask_volume"] = total_ask_qty
            quote["bid_ask_ratio"] = bid_ask_ratio
            quote["orderbook_received_at"] = timestamp_text
            quote["orderbook_source"] = orderbook_source
            events = self._orderbook_events.setdefault(
                code, deque(maxlen=self._orderbook_event_limit)
            )
            event = self._event(code, values, timestamp_text, sequence)
            event["bid_volume"] = total_bid_qty
            event["ask_volume"] = total_ask_qty
            event["bid_ask_ratio"] = bid_ask_ratio
            event["orderbook_received_at"] = timestamp_text
            event["orderbook_source"] = orderbook_source
            events.append(event)
            return deepcopy(quote)

    def update_close_metrics(self, stock_code, metrics):
        if not isinstance(metrics, dict):
            raise TypeError("metrics must be a dict")
        code = self._normalized_code(stock_code)
        timestamp = time.time()
        timestamp_text = self._timestamp_text(timestamp)
        bid_volume = metrics.get("bid_volume_snapshot")
        ask_volume = metrics.get("ask_volume_snapshot")
        bid_pct = metrics.get("bid_pct")
        ask_pct = metrics.get("ask_pct")
        if bid_pct is None or ask_pct is None:
            bid_pct, ask_pct = self._bid_ask_pct(bid_volume, ask_volume)
        bid_ask_ratio = metrics.get("bid_ask_ratio_snapshot")
        if bid_ask_ratio is None:
            bid_ask_ratio = self._snapshot_ratio(bid_volume, ask_volume)
        values = {
            "stock_code": code,
            "bid_volume_snapshot": bid_volume,
            "ask_volume_snapshot": ask_volume,
            "bid_pct": bid_pct,
            "ask_pct": ask_pct,
            "bid_ask_ratio_snapshot": bid_ask_ratio,
            "orderbook_source": metrics.get("orderbook_source"),
            "orderbook_snapshot_at": (
                metrics.get("orderbook_snapshot_at") or timestamp_text
            ),
            "orderbook_status": metrics.get("orderbook_status"),
            "orderbook_error": metrics.get("orderbook_error"),
            "orderbook_status_detail": metrics.get(
                "orderbook_status_detail"
            ),
            "orderbook_requested_at": metrics.get("orderbook_requested_at"),
            "orderbook_completed_at": metrics.get("orderbook_completed_at"),
            "orderbook_tr_repeat_count": metrics.get("orderbook_tr_repeat_count"),
            "orderbook_raw_sample": metrics.get("orderbook_raw_sample"),
            "orderbook_rqname": metrics.get("orderbook_rqname"),
            "orderbook_trcode": metrics.get("orderbook_trcode"),
            "orderbook_screen_no": metrics.get("orderbook_screen_no"),
            "realtime_strength_snapshot": metrics.get(
                "realtime_strength_snapshot"
            ),
            "strength_5m": metrics.get("strength_5m"),
            "strength_20m": metrics.get("strength_20m"),
            "strength_60m": metrics.get("strength_60m"),
            "strength_source": metrics.get("strength_source"),
            "strength_snapshot_at": (
                metrics.get("strength_snapshot_at") or timestamp_text
            ),
            "strength_status": metrics.get("strength_status"),
            "strength_error": metrics.get("strength_error"),
            "strength_status_detail": metrics.get("strength_status_detail"),
            "strength_requested_at": metrics.get("strength_requested_at"),
            "strength_completed_at": metrics.get("strength_completed_at"),
            "strength_tr_repeat_count": metrics.get("strength_tr_repeat_count"),
            "strength_raw_sample": metrics.get("strength_raw_sample"),
            "strength_rqname": metrics.get("strength_rqname"),
            "strength_trcode": metrics.get("strength_trcode"),
            "strength_screen_no": metrics.get("strength_screen_no"),
            "status_detail": metrics.get("status_detail"),
            "updated_at": timestamp_text,
        }
        with self._lock:
            previous = self._close_metric_snapshots.get(code, {})
            clearable_none_fields = {
                "orderbook_error",
                "orderbook_status_detail",
                "strength_error",
                "strength_status_detail",
            }
            merged_values = {
                key: value
                for key, value in values.items()
                if value is not None
                or (key in clearable_none_fields and key in metrics)
            }
            merged = {**previous, **merged_values}
            orderbook_at = merged.get("orderbook_snapshot_at")
            strength_at = merged.get("strength_snapshot_at")
            merged["orderbook_stale_sec"] = self._age_from_now(orderbook_at)
            merged["strength_stale_sec"] = self._age_from_now(strength_at)
            self._close_metric_snapshots[code] = merged
            quote = self._ensure_quote(code)
            quote.update({key: value for key, value in merged.items() if value is not None})
            return deepcopy(merged)

    @staticmethod
    def _age_from_now(timestamp_text):
        if not timestamp_text:
            return None
        try:
            timestamp = datetime.fromisoformat(
                str(timestamp_text).replace("Z", "+00:00")
            )
            now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
            return max(0.0, round((now - timestamp).total_seconds(), 3))
        except (TypeError, ValueError):
            return None

    def close_metrics_snapshot(self, stock_codes=None):
        with self._lock:
            if stock_codes is None:
                codes = tuple(self._close_metric_snapshots)
            else:
                codes = tuple(
                    dict.fromkeys(self._normalized_code(code) for code in stock_codes)
                )
            snapshots = {}
            for code in codes:
                if code not in self._close_metric_snapshots:
                    continue
                snapshot = deepcopy(self._close_metric_snapshots[code])
                snapshot["orderbook_stale_sec"] = self._age_from_now(
                    snapshot.get("orderbook_snapshot_at")
                )
                snapshot["strength_stale_sec"] = self._age_from_now(
                    snapshot.get("strength_snapshot_at")
                )
                snapshots[code] = snapshot
            return snapshots

    def update_foreign_line(self, stock_code, foreign_line_raw):
        code = self._normalized_code(stock_code)
        with self._lock:
            quote, _, _ = self._apply_update(
                code, {"foreign_line_raw": foreign_line_raw}
            )
            return deepcopy(quote)

    def _snapshot_for_codes(self, stock_codes):
        return {
            "sequence": self._sequence,
            "updated_at": self._updated_at,
            "quotes": {
                code: deepcopy(self._quotes[code])
                for code in stock_codes
                if code in self._quotes
            },
            "trade_events": {
                code: deepcopy(list(self._trade_events[code]))
                for code in stock_codes
                if code in self._trade_events
            },
            "orderbook_events": {
                code: deepcopy(list(self._orderbook_events[code]))
                for code in stock_codes
                if code in self._orderbook_events
            },
            "close_metrics": {
                code: deepcopy(self._close_metric_snapshots[code])
                for code in stock_codes
                if code in self._close_metric_snapshots
            },
        }

    def snapshot(self):
        with self._lock:
            return self._snapshot_for_codes(tuple(self._quotes))

    def snapshot_many(self, stock_codes):
        codes = tuple(dict.fromkeys(self._normalized_code(code) for code in stock_codes))
        with self._lock:
            return self._snapshot_for_codes(codes)

    def snapshot_quotes_only(self, stock_codes=None):
        if stock_codes is None:
            with self._lock:
                codes = tuple(self._quotes)
                return {
                    "sequence": self._sequence,
                    "updated_at": self._updated_at,
                    "quotes": {
                        code: dict(self._quotes[code])
                        for code in codes
                        if code in self._quotes
                    },
                }

        codes = tuple(dict.fromkeys(self._normalized_code(code) for code in stock_codes))
        with self._lock:
            return {
                "sequence": self._sequence,
                "updated_at": self._updated_at,
                "quotes": {
                    code: dict(self._quotes[code])
                    for code in codes
                    if code in self._quotes
                },
            }

    def snapshot_quotes_since(self, since_sequence, stock_codes=None):
        since_sequence = int(since_sequence)
        if since_sequence < 0:
            raise ValueError("since_sequence must be non-negative")
        if stock_codes is None:
            with self._lock:
                if since_sequence > self._sequence:
                    raise ValueError("sequence_ahead")
                codes = tuple(self._quotes)
                return {
                    "sequence": self._sequence,
                    "updated_at": self._updated_at,
                    "quotes": {
                        code: dict(self._quotes[code])
                        for code in codes
                        if code in self._quotes
                        and (
                            self._quotes[code].get("sequence") is None
                            or self._quotes[code].get("sequence") > since_sequence
                        )
                    },
                }

        codes = tuple(dict.fromkeys(self._normalized_code(code) for code in stock_codes))
        with self._lock:
            if since_sequence > self._sequence:
                raise ValueError("sequence_ahead")
            return {
                "sequence": self._sequence,
                "updated_at": self._updated_at,
                "quotes": {
                    code: dict(self._quotes[code])
                    for code in codes
                    if code in self._quotes
                    and (
                        self._quotes[code].get("sequence") is None
                        or self._quotes[code].get("sequence") > since_sequence
                    )
                },
            }

    def remove_stale(self, max_age_seconds, now=None):
        if max_age_seconds < 0:
            raise ValueError("max_age_seconds must be non-negative")
        current_time = time.time() if now is None else float(now)
        cutoff = current_time - float(max_age_seconds)
        with self._lock:
            stale_codes = sorted(
                code for code, timestamp in self._last_seen.items() if timestamp < cutoff
            )
            for code in stale_codes:
                self._quotes.pop(code, None)
                self._trade_events.pop(code, None)
                self._orderbook_events.pop(code, None)
                self._last_seen.pop(code, None)
            return stale_codes

    def clear_orderbook_events(self):
        with self._lock:
            self._orderbook_events.clear()

    def clear(self):
        with self._lock:
            self._quotes.clear()
            self._trade_events.clear()
            self._orderbook_events.clear()
            self._close_metric_snapshots.clear()
            self._last_seen.clear()
            self._base_ohlc.clear()
            self._sequence = 0
            self._updated_at = None


def _load_tradable_stock_codes():
    master_path = DOCS_DIR / "tradable_stock_master.csv"
    with master_path.open("r", encoding="utf-8-sig", newline="") as master_file:
        reader = csv.reader(master_file)
        next(reader, None)
        return {
            code
            for row in reader
            if row and (code := _stock_code(row[0]))
        }
