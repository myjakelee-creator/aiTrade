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
    "total_bid_qty",
    "total_ask_qty",
    "bid_ask_ratio",
    "foreign_line_raw",
    "cumulative_volume",
    "cumulative_value",
    "received_code",
    "normalized_code",
    "registered_code",
    "original_registered_code",
    "realtime_source_code",
    "source_code",
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
        }
        with self._lock:
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
    ):
        code = self._normalized_code(stock_code)
        if orderbook is not None:
            if not isinstance(orderbook, dict):
                raise TypeError("orderbook must be a dict")
            best_bid = orderbook.get("best_bid_price", best_bid)
            best_ask = orderbook.get("best_ask_price", best_ask)
            total_bid_qty = orderbook.get("bid_volume", total_bid_qty)
            total_ask_qty = orderbook.get("ask_volume", total_ask_qty)
        values = {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "total_bid_qty": total_bid_qty,
            "total_ask_qty": total_ask_qty,
            "bid_ask_ratio": bid_ask_ratio,
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
            events = self._orderbook_events.setdefault(
                code, deque(maxlen=self._orderbook_event_limit)
            )
            events.append(self._event(code, values, timestamp_text, sequence))
            return deepcopy(quote)

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

    def clear(self):
        with self._lock:
            self._quotes.clear()
            self._trade_events.clear()
            self._orderbook_events.clear()
            self._last_seen.clear()
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
