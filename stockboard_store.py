import csv
import json
import os
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock

from stockboard_engine import _stock_code


DOCS_DIR = Path(__file__).resolve().parent / "docs"
RUNTIME_DIR = Path(__file__).resolve().parent / "data" / "runtime"
CLOSE_METRICS_PERSIST_PATH = (
    RUNTIME_DIR / "stockboard_close_metrics_snapshots.json"
)
ONE_MIN_TRADE_WINDOW_SEC = 60
ONE_MIN_TRADE_COMPARE_WINDOW_SEC = 60
ONE_MIN_TRADE_BUCKET_RETENTION_SEC = (
    ONE_MIN_TRADE_WINDOW_SEC + ONE_MIN_TRADE_COMPARE_WINDOW_SEC
)
KRW_PER_EOK = 100_000_000
LARGE_TRADE_THRESHOLD_KRW = 50_000_000
LARGE_TRADE_THRESHOLD_EOK = LARGE_TRADE_THRESHOLD_KRW / KRW_PER_EOK

QUOTE_FIELDS = (
    "price",
    "change_rate",
    "trade_price",
    "trade_qty",
    "trade_side",
    "trade_time",
    "fid20_trade_time",
    "trade_lag_sec",
    "fid20_trade_lag_sec",
    "stale_trade_suspect",
    "execution_strength",
    "price_received_at",
    "price_updated_at",
    "price_sequence",
    "trade_received_at",
    "trade_updated_at",
    "trade_sequence",
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
    "orderbook_updated_at",
    "orderbook_sequence",
    "orderbook_age_sec",
    "orderbook_source",
    "realtime_strength_snapshot",
    "session_buy_qty_live",
    "session_sell_qty_live",
    "session_strength",
    "session_strength_source",
    "one_min_strength",
    "one_min_buy_qty",
    "one_min_sell_qty",
    "one_min_buy_value_eok",
    "one_min_sell_value_eok",
    "one_min_net_buy_value_eok",
    "prev_one_min_net_buy_value_eok",
    "one_min_net_buy_value_delta_eok",
    "one_min_trade_value_eok",
    "prev_one_min_trade_value_eok",
    "one_min_trade_value_delta_eok",
    "prev_one_min_strength",
    "one_min_strength_delta",
    "one_min_strength_growth_rate",
    "large_trade_threshold_krw",
    "large_trade_threshold_eok",
    "large_trade_buy_count",
    "large_trade_sell_count",
    "large_trade_net_count",
    "prev_large_trade_net_count",
    "large_trade_net_count_delta",
    "large_trade_buy_sum_eok",
    "large_trade_sell_sum_eok",
    "large_trade_net_sum_eok",
    "prev_large_trade_net_sum_eok",
    "large_trade_net_sum_delta_eok",
    "large_trade_unknown_count",
    "large_trade_unknown_sum_eok",
    "large_trade_source",
    "large_trade_updated_at",
    "large_trade_status",
    "big_hand_buy_count_1eok",
    "big_hand_sell_count_1eok",
    "big_hand_net_buy_count_1eok",
    "big_hand_buy_sum_eok",
    "big_hand_sell_sum_eok",
    "big_hand_net_sum_eok",
    "prev_big_hand_net_buy_count_1eok",
    "big_hand_net_buy_count_delta_1eok",
    "prev_big_hand_net_sum_eok",
    "big_hand_net_sum_delta_eok",
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
    "regular_close_price",
    "regular_close_change_rate",
    "regular_close_ohlc",
    "regular_close_snapshot_at",
    "regular_close_snapshot_source",
    "regular_close_snapshot_status",
)


class RealtimeStore:
    _STRENGTH_PERSIST_FIELDS = (
        "stock_code",
        "updated_at",
        "realtime_strength_snapshot",
        "strength_5m",
        "strength_20m",
        "strength_60m",
        "strength_source",
        "strength_snapshot_at",
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
    )
    _ORDERBOOK_PERSIST_FIELDS = (
        "stock_code",
        "updated_at",
        "bid_volume_snapshot",
        "ask_volume_snapshot",
        "bid_pct",
        "ask_pct",
        "bid_ask_ratio_snapshot",
        "orderbook_source",
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
    )
    _LARGE_TRADE_PERSIST_FIELDS = (
        "stock_code",
        "updated_at",
        "large_trade_source",
        "large_trade_threshold_krw",
        "large_trade_threshold_eok",
        "large_trade_buy_count",
        "large_trade_sell_count",
        "large_trade_net_count",
        "large_trade_buy_sum_eok",
        "large_trade_sell_sum_eok",
        "large_trade_net_sum_eok",
        "large_trade_unknown_count",
        "large_trade_unknown_sum_eok",
        "large_trade_updated_at",
        "large_trade_status",
        "big_hand_buy_count_1eok",
        "big_hand_sell_count_1eok",
        "big_hand_net_buy_count_1eok",
        "big_hand_buy_sum_eok",
        "big_hand_sell_sum_eok",
        "big_hand_net_sum_eok",
    )

    def __init__(
        self,
        trade_event_limit=200,
        orderbook_event_limit=200,
        close_metrics_persist_path=CLOSE_METRICS_PERSIST_PATH,
    ):
        trade_event_limit = int(trade_event_limit)
        orderbook_event_limit = int(orderbook_event_limit)
        if trade_event_limit <= 0 or orderbook_event_limit <= 0:
            raise ValueError("event limits must be positive integers")
        self._lock = RLock()
        self._quotes = {}
        self._trade_events = {}
        self._trade_windows = {}
        self._orderbook_events = {}
        self._close_metric_snapshots = {}
        self._close_metrics_persistent_snapshots = {}
        self._base_ohlc = {}
        self._last_seen = {}
        self._trade_event_limit = trade_event_limit
        self._orderbook_event_limit = orderbook_event_limit
        self._sequence = 0
        self._updated_at = None
        self._latest_only_enabled = True
        self._store_update_guard_drop_count = 0
        self._older_trade_drop_count = 0
        self._latest_only_dropped_count = 0
        self._one_min_bucket_enabled = True
        self._one_min_bucket_pruned_count = 0
        self._one_min_bucket_update_count = 0
        self._one_min_bucket_last_update_at = None
        self._close_metrics_persist_path = Path(close_metrics_persist_path)
        self._close_metrics_persist_error = None
        self._load_persistent_close_metrics()

    @staticmethod
    def _normalized_code(stock_code):
        code = _stock_code(stock_code)
        if code is None:
            raise ValueError(f"invalid stock code: {stock_code!r}")
        return code

    @staticmethod
    def _timestamp_text(timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()

    @staticmethod
    def _kst_now():
        return datetime.now(timezone(timedelta(hours=9)))

    @staticmethod
    def _has_persistent_value(value):
        return value is not None and value != ""

    def _persistent_timestamp(self):
        return datetime.now(timezone.utc).isoformat()

    def _merge_persistent_values(self, previous, values, fields):
        merged = dict(previous or {})
        for field in fields:
            if field not in values:
                continue
            value = values.get(field)
            if self._has_persistent_value(value):
                merged[field] = deepcopy(value)
        return merged

    def _load_persistent_close_metrics(self):
        path = self._close_metrics_persist_path
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as snapshot_file:
                payload = json.load(snapshot_file)
        except Exception as error:
            self._close_metrics_persist_error = f"load failed: {error}"
            print(
                f"warning: close metrics persistent load failed: {error}",
                flush=True,
            )
            return
        snapshots = payload.get("snapshots") if isinstance(payload, dict) else None
        if not isinstance(snapshots, dict):
            self._close_metrics_persist_error = "load failed: invalid snapshots"
            return
        restored_at = self._persistent_timestamp()
        restored = {}
        for raw_code, snapshot in snapshots.items():
            if not isinstance(snapshot, dict):
                continue
            try:
                code = self._normalized_code(
                    snapshot.get("stock_code") or raw_code
                )
            except ValueError:
                continue
            restored_snapshot = {
                key: deepcopy(value)
                for key, value in snapshot.items()
                if self._has_persistent_value(value)
            }
            restored_snapshot["stock_code"] = code
            restored_snapshot["close_metrics_persistent"] = True
            restored_snapshot["close_metrics_restored_at"] = restored_at
            if (
                restored_snapshot.get("strength_source") == "opt10046"
                and not self._has_persistent_value(
                    restored_snapshot.get("strength_status")
                )
            ):
                restored_snapshot["strength_status"] = "restored_persistent"
            if (
                restored_snapshot.get("orderbook_source") == "opt10004"
                and not self._has_persistent_value(
                    restored_snapshot.get("orderbook_status")
                )
            ):
                restored_snapshot["orderbook_status"] = "restored_persistent"
            restored[code] = restored_snapshot
        self._close_metric_snapshots.update(restored)
        self._close_metrics_persistent_snapshots.update(deepcopy(restored))
        self._close_metrics_persist_error = None

    def _write_persistent_close_metrics(self):
        path = self._close_metrics_persist_path
        payload = {
            "version": 1,
            "saved_at": self._persistent_timestamp(),
            "snapshots": dict(sorted(self._close_metrics_persistent_snapshots.items())),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_name(f"{path.name}.tmp")
            with tmp_path.open("w", encoding="utf-8") as snapshot_file:
                json.dump(payload, snapshot_file, ensure_ascii=False, indent=2)
                snapshot_file.write("\n")
            os.replace(tmp_path, path)
            self._close_metrics_persist_error = None
        except Exception as error:
            self._close_metrics_persist_error = f"save failed: {error}"
            print(
                f"warning: close metrics persistent save failed: {error}",
                flush=True,
            )

    def _persist_close_metrics_snapshot(self, code, snapshot):
        persisted_at = self._persistent_timestamp()
        previous = self._close_metrics_persistent_snapshots.get(code, {})
        persistent_snapshot = dict(previous)
        if snapshot.get("strength_source") == "opt10046":
            persistent_snapshot = self._merge_persistent_values(
                persistent_snapshot,
                snapshot,
                self._STRENGTH_PERSIST_FIELDS,
            )
        if snapshot.get("orderbook_source") == "opt10004":
            persistent_snapshot = self._merge_persistent_values(
                persistent_snapshot,
                snapshot,
                self._ORDERBOOK_PERSIST_FIELDS,
            )
        if snapshot.get("large_trade_source") == "opt10055_day":
            persistent_snapshot = self._merge_persistent_values(
                persistent_snapshot,
                snapshot,
                self._LARGE_TRADE_PERSIST_FIELDS,
            )
        if (
            persistent_snapshot.get("strength_source") != "opt10046"
            and persistent_snapshot.get("orderbook_source") != "opt10004"
            and persistent_snapshot.get("large_trade_source") != "opt10055_day"
        ):
            return
        persistent_snapshot["stock_code"] = code
        persistent_snapshot["close_metrics_persistent"] = True
        persistent_snapshot["close_metrics_persisted_at"] = persisted_at
        self._close_metrics_persistent_snapshots[code] = persistent_snapshot
        self._write_persistent_close_metrics()

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

    def _apply_update(self, stock_code, values, timestamp_text=None):
        timestamp = time.time()
        if timestamp_text is None:
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
    def _timestamp_value(timestamp_text):
        if not timestamp_text:
            return None
        try:
            timestamp = datetime.fromisoformat(
                str(timestamp_text).replace("Z", "+00:00")
            )
            return timestamp.timestamp()
        except (TypeError, ValueError):
            return None

    def _is_older_trade_update(
        self,
        quote,
        *,
        incoming_received_at=None,
        incoming_sequence=None,
    ):
        if incoming_sequence is not None:
            try:
                incoming_sequence = int(incoming_sequence)
                current_sequence = int(
                    quote.get("price_sequence")
                    or quote.get("trade_sequence")
                    or 0
                )
            except (TypeError, ValueError):
                incoming_sequence = None
                current_sequence = 0
            if incoming_sequence is not None and current_sequence > 0:
                return incoming_sequence < current_sequence

        incoming_timestamp = self._timestamp_value(incoming_received_at)
        if incoming_timestamp is None:
            return False
        current_timestamps = [
            self._timestamp_value(quote.get("price_received_at")),
            self._timestamp_value(quote.get("trade_received_at")),
        ]
        current_timestamps = [
            value for value in current_timestamps if value is not None
        ]
        return bool(current_timestamps and incoming_timestamp < max(current_timestamps))

    def _record_trade_guard_drop(self):
        self._store_update_guard_drop_count += 1
        self._older_trade_drop_count += 1
        self._latest_only_dropped_count += 1

    def _has_fresh_realtime_large_trade(self, quote):
        if not isinstance(quote, dict):
            return False
        if quote.get("large_trade_source") != "realtime_bucket":
            return False
        timestamp = self._timestamp_value(quote.get("large_trade_updated_at"))
        if timestamp is None:
            return False
        return (time.time() - timestamp) <= ONE_MIN_TRADE_WINDOW_SEC

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
    def _mark_price_lane(quote, timestamp_text, sequence):
        quote["price_received_at"] = timestamp_text
        quote["price_updated_at"] = timestamp_text
        quote["price_sequence"] = sequence
        quote["trade_received_at"] = timestamp_text
        quote["trade_updated_at"] = timestamp_text
        quote["trade_sequence"] = sequence

    @staticmethod
    def _mark_orderbook_lane(quote, timestamp_text, sequence):
        quote["orderbook_received_at"] = timestamp_text
        quote["orderbook_updated_at"] = timestamp_text
        quote["orderbook_sequence"] = sequence

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
    def _trade_qty_number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _new_one_min_bucket():
        return {
            "buy_qty": 0,
            "sell_qty": 0,
            "buy_value_eok": 0,
            "sell_value_eok": 0,
            "large_buy_count": 0,
            "large_sell_count": 0,
            "large_unknown_count": 0,
            "large_buy_sum_eok": 0,
            "large_sell_sum_eok": 0,
            "large_unknown_sum_eok": 0,
        }

    @classmethod
    def _sum_one_min_buckets(cls, buckets, start_sec, end_sec):
        total = cls._new_one_min_bucket()
        for bucket_sec, bucket in buckets.items():
            if bucket_sec < start_sec or bucket_sec > end_sec:
                continue
            for key in total:
                total[key] += bucket.get(key) or 0
        return total

    @classmethod
    def _one_min_trade_metrics_from_bucket(
        cls,
        window_state,
        *,
        bucket_sec,
        trade_price=None,
        trade_qty=None,
        include_trade=True,
    ):
        buckets = window_state["buckets"]
        prune_before = bucket_sec - ONE_MIN_TRADE_BUCKET_RETENTION_SEC + 1
        expired_keys = [
            key for key in tuple(buckets) if key < prune_before
        ]
        for key in expired_keys:
            buckets.pop(key, None)

        if include_trade:
            qty_number = cls._trade_qty_number(trade_qty)
            price_number = cls._price_number(trade_price)
            if qty_number:
                bucket = buckets.setdefault(bucket_sec, cls._new_one_min_bucket())
                qty_abs = abs(qty_number)
                trade_amount_krw = 0
                trade_amount_eok = 0
                if price_number is not None:
                    trade_amount_krw = price_number * qty_abs
                    trade_amount_eok = trade_amount_krw / KRW_PER_EOK
                is_large_trade = trade_amount_krw >= LARGE_TRADE_THRESHOLD_KRW
                if qty_number > 0:
                    bucket["buy_qty"] += qty_abs
                    bucket["buy_value_eok"] += trade_amount_eok
                    if is_large_trade:
                        bucket["large_buy_count"] += 1
                        bucket["large_buy_sum_eok"] += trade_amount_eok
                elif qty_number < 0:
                    bucket["sell_qty"] += qty_abs
                    bucket["sell_value_eok"] += trade_amount_eok
                    if is_large_trade:
                        bucket["large_sell_count"] += 1
                        bucket["large_sell_sum_eok"] += trade_amount_eok
                elif is_large_trade:
                    bucket["large_unknown_count"] += 1
                    bucket["large_unknown_sum_eok"] += trade_amount_eok

        recent = cls._sum_one_min_buckets(
            buckets,
            bucket_sec - ONE_MIN_TRADE_WINDOW_SEC + 1,
            bucket_sec,
        )
        previous = cls._sum_one_min_buckets(
            buckets,
            bucket_sec
            - ONE_MIN_TRADE_WINDOW_SEC
            - ONE_MIN_TRADE_COMPARE_WINDOW_SEC
            + 1,
            bucket_sec - ONE_MIN_TRADE_WINDOW_SEC,
        )
        current_strength = cls._session_strength(
            recent["buy_qty"], recent["sell_qty"]
        )
        previous_strength = cls._session_strength(
            previous["buy_qty"], previous["sell_qty"]
        )
        strength_delta = None
        strength_growth_rate = None
        if current_strength is not None and previous_strength is not None:
            strength_delta = round(current_strength - previous_strength, 4)
            if previous_strength:
                strength_growth_rate = round(
                    (strength_delta / previous_strength) * 100,
                    4,
                )

        buy_value = recent["buy_value_eok"]
        sell_value = recent["sell_value_eok"]
        prev_buy_value = previous["buy_value_eok"]
        prev_sell_value = previous["sell_value_eok"]
        net_buy_value = buy_value - sell_value
        prev_net_buy_value = prev_buy_value - prev_sell_value
        trade_value = buy_value + sell_value
        prev_trade_value = prev_buy_value + prev_sell_value
        large_net_count = recent["large_buy_count"] - recent["large_sell_count"]
        prev_large_net_count = (
            previous["large_buy_count"] - previous["large_sell_count"]
        )
        large_net_sum = (
            recent["large_buy_sum_eok"] - recent["large_sell_sum_eok"]
        )
        prev_large_net_sum = (
            previous["large_buy_sum_eok"] - previous["large_sell_sum_eok"]
        )

        return len(expired_keys), {
            "one_min_strength": current_strength,
            "one_min_buy_qty": max(0, recent["buy_qty"]),
            "one_min_sell_qty": max(0, recent["sell_qty"]),
            "one_min_buy_value_eok": round(max(0, buy_value), 4),
            "one_min_sell_value_eok": round(max(0, sell_value), 4),
            "one_min_net_buy_value_eok": round(net_buy_value, 4),
            "prev_one_min_net_buy_value_eok": round(prev_net_buy_value, 4),
            "one_min_net_buy_value_delta_eok": round(
                net_buy_value - prev_net_buy_value,
                4,
            ),
            "one_min_trade_value_eok": round(trade_value, 4),
            "prev_one_min_trade_value_eok": round(prev_trade_value, 4),
            "one_min_trade_value_delta_eok": round(
                trade_value - prev_trade_value,
                4,
            ),
            "prev_one_min_strength": previous_strength,
            "one_min_strength_delta": strength_delta,
            "one_min_strength_growth_rate": strength_growth_rate,
            "large_trade_threshold_krw": LARGE_TRADE_THRESHOLD_KRW,
            "large_trade_threshold_eok": LARGE_TRADE_THRESHOLD_EOK,
            "large_trade_buy_count": max(0, recent["large_buy_count"]),
            "large_trade_sell_count": max(0, recent["large_sell_count"]),
            "large_trade_net_count": large_net_count,
            "prev_large_trade_net_count": prev_large_net_count,
            "large_trade_net_count_delta": (
                large_net_count - prev_large_net_count
            ),
            "large_trade_buy_sum_eok": round(
                max(0, recent["large_buy_sum_eok"]), 4
            ),
            "large_trade_sell_sum_eok": round(
                max(0, recent["large_sell_sum_eok"]), 4
            ),
            "large_trade_net_sum_eok": round(large_net_sum, 4),
            "prev_large_trade_net_sum_eok": round(prev_large_net_sum, 4),
            "large_trade_net_sum_delta_eok": round(
                large_net_sum - prev_large_net_sum,
                4,
            ),
            "large_trade_unknown_count": max(0, recent["large_unknown_count"]),
            "large_trade_unknown_sum_eok": round(
                max(0, recent["large_unknown_sum_eok"]), 4
            ),
            "large_trade_source": "realtime_bucket",
            "large_trade_updated_at": cls._timestamp_text(bucket_sec),
            "large_trade_status": "ok",
            # Legacy alias: names say 1eok/big_hand, values now use 50m threshold.
            "big_hand_buy_count_1eok": max(0, recent["large_buy_count"]),
            "big_hand_sell_count_1eok": max(0, recent["large_sell_count"]),
            "big_hand_net_buy_count_1eok": large_net_count,
            "big_hand_buy_sum_eok": round(
                max(0, recent["large_buy_sum_eok"]), 4
            ),
            "big_hand_sell_sum_eok": round(
                max(0, recent["large_sell_sum_eok"]), 4
            ),
            "big_hand_net_sum_eok": round(large_net_sum, 4),
            "prev_big_hand_net_buy_count_1eok": prev_large_net_count,
            "big_hand_net_buy_count_delta_1eok": (
                large_net_count - prev_large_net_count
            ),
            "prev_big_hand_net_sum_eok": round(prev_large_net_sum, 4),
            "big_hand_net_sum_delta_eok": round(
                large_net_sum - prev_large_net_sum,
                4,
            ),
        }

    @staticmethod
    def _new_trade_window_state():
        return {
            "buckets": {},
        }

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
        trade_lag_sec=None,
        fid20_trade_lag_sec=None,
        stale_trade_suspect=None,
        received_at=None,
        incoming_sequence=None,
    ):
        code = self._normalized_code(stock_code)
        values = {
            "price": price,
            "change_rate": change_rate,
            "trade_price": trade_price,
            "trade_qty": trade_qty,
            "trade_side": trade_side,
            "trade_time": trade_time,
            "fid20_trade_time": trade_time,
            "trade_lag_sec": trade_lag_sec,
            "fid20_trade_lag_sec": fid20_trade_lag_sec,
            "stale_trade_suspect": stale_trade_suspect,
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
        timestamp_text = received_at or None
        if price_first:
            first_values = {
                key: values.get(key)
                for key in (
                    "price",
                    "change_rate",
                    "trade_qty",
                    "trade_time",
                    "fid20_trade_time",
                    "trade_lag_sec",
                    "fid20_trade_lag_sec",
                    "stale_trade_suspect",
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
                quote = self._ensure_quote(code)
                if self._is_older_trade_update(
                    quote,
                    incoming_received_at=timestamp_text,
                    incoming_sequence=incoming_sequence,
                ):
                    self._record_trade_guard_drop()
                    return deepcopy(quote)
                quote, timestamp_text, sequence = self._apply_update(
                    code, first_values, timestamp_text=timestamp_text
                )
                self._mark_price_lane(quote, timestamp_text, sequence)
        with self._lock:
            quote = self._ensure_quote(code)
            if self._is_older_trade_update(
                quote,
                incoming_received_at=timestamp_text,
                incoming_sequence=incoming_sequence,
            ):
                self._record_trade_guard_drop()
                return deepcopy(quote)
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
            trade_qty_number = self._trade_qty_number(trade_qty)
            if trade_qty_number > 0:
                session_buy_qty_live += trade_qty_number
            elif trade_qty_number < 0:
                session_sell_qty_live += abs(trade_qty_number)
            trade_window = self._trade_windows.get(code)
            if trade_window is None:
                trade_window = self._new_trade_window_state()
                self._trade_windows[code] = trade_window
            rolling_trade_price = trade_price
            if rolling_trade_price is None:
                rolling_trade_price = price
            bucket_timestamp = self._timestamp_value(timestamp_text)
            if bucket_timestamp is None:
                bucket_timestamp = time.time()
            bucket_sec = int(bucket_timestamp)
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
            bucket_pruned_count, bucket_metrics = (
                self._one_min_trade_metrics_from_bucket(
                    trade_window,
                    bucket_sec=bucket_sec,
                    trade_price=rolling_trade_price,
                    trade_qty=trade_qty,
                    include_trade=not bool(stale_trade_suspect),
                )
            )
            self._one_min_bucket_pruned_count += bucket_pruned_count
            if not bool(stale_trade_suspect) and trade_qty_number:
                self._one_min_bucket_update_count += 1
                self._one_min_bucket_last_update_at = timestamp_text
            values.update(bucket_metrics)
            quote, timestamp_text, sequence = self._apply_update(
                code, values, timestamp_text=timestamp_text
            )
            self._mark_price_lane(quote, timestamp_text, sequence)
            events = self._trade_events.setdefault(
                code, deque(maxlen=self._trade_event_limit)
            )
            event = self._event(code, values, timestamp_text, sequence)
            event["price_received_at"] = timestamp_text
            event["price_updated_at"] = timestamp_text
            event["price_sequence"] = sequence
            event["trade_received_at"] = timestamp_text
            event["trade_updated_at"] = timestamp_text
            event["trade_sequence"] = sequence
            events.append(event)
            return deepcopy(quote)

    def latest_only_diagnostics(self):
        with self._lock:
            return {
                "latest_only_enabled": self._latest_only_enabled,
                "older_trade_drop_count": self._older_trade_drop_count,
                "latest_only_dropped_count": self._latest_only_dropped_count,
                "store_update_guard_drop_count": (
                    self._store_update_guard_drop_count
                ),
            }

    def one_min_bucket_diagnostics(self):
        with self._lock:
            total_bucket_count = 0
            for window_state in self._trade_windows.values():
                buckets = window_state.get("buckets")
                if isinstance(buckets, dict):
                    total_bucket_count += len(buckets)
            return {
                "one_min_bucket_enabled": self._one_min_bucket_enabled,
                "one_min_bucket_code_count": len(self._trade_windows),
                "one_min_bucket_total_bucket_count": total_bucket_count,
                "one_min_bucket_pruned_count": self._one_min_bucket_pruned_count,
                "one_min_bucket_update_count": self._one_min_bucket_update_count,
                "one_min_bucket_last_update_at": (
                    self._one_min_bucket_last_update_at
                ),
                "one_min_bucket_window_sec": ONE_MIN_TRADE_WINDOW_SEC,
                "one_min_bucket_compare_window_sec": (
                    ONE_MIN_TRADE_COMPARE_WINDOW_SEC
                ),
            }

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
            self._mark_orderbook_lane(quote, timestamp_text, sequence)
            quote["orderbook_source"] = orderbook_source
            events = self._orderbook_events.setdefault(
                code, deque(maxlen=self._orderbook_event_limit)
            )
            event = self._event(code, values, timestamp_text, sequence)
            event["bid_volume"] = total_bid_qty
            event["ask_volume"] = total_ask_qty
            event["bid_ask_ratio"] = bid_ask_ratio
            event["orderbook_received_at"] = timestamp_text
            event["orderbook_updated_at"] = timestamp_text
            event["orderbook_sequence"] = sequence
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
            "large_trade_source": metrics.get("large_trade_source"),
            "large_trade_threshold_krw": metrics.get(
                "large_trade_threshold_krw"
            ),
            "large_trade_threshold_eok": metrics.get(
                "large_trade_threshold_eok"
            ),
            "large_trade_buy_count": metrics.get("large_trade_buy_count"),
            "large_trade_sell_count": metrics.get("large_trade_sell_count"),
            "large_trade_net_count": metrics.get("large_trade_net_count"),
            "large_trade_buy_sum_eok": metrics.get("large_trade_buy_sum_eok"),
            "large_trade_sell_sum_eok": metrics.get(
                "large_trade_sell_sum_eok"
            ),
            "large_trade_net_sum_eok": metrics.get("large_trade_net_sum_eok"),
            "large_trade_unknown_count": metrics.get(
                "large_trade_unknown_count"
            ),
            "large_trade_unknown_sum_eok": metrics.get(
                "large_trade_unknown_sum_eok"
            ),
            "large_trade_updated_at": metrics.get(
                "large_trade_updated_at"
            ),
            "large_trade_status": metrics.get("large_trade_status"),
            "big_hand_buy_count_1eok": metrics.get(
                "big_hand_buy_count_1eok"
            ),
            "big_hand_sell_count_1eok": metrics.get(
                "big_hand_sell_count_1eok"
            ),
            "big_hand_net_buy_count_1eok": metrics.get(
                "big_hand_net_buy_count_1eok"
            ),
            "big_hand_buy_sum_eok": metrics.get("big_hand_buy_sum_eok"),
            "big_hand_sell_sum_eok": metrics.get("big_hand_sell_sum_eok"),
            "big_hand_net_sum_eok": metrics.get("big_hand_net_sum_eok"),
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
            self._persist_close_metrics_snapshot(code, merged)
            quote = self._ensure_quote(code)
            quote_values = {
                key: value for key, value in merged.items() if value is not None
            }
            if (
                merged.get("large_trade_source") == "opt10055_day"
                and self._has_fresh_realtime_large_trade(quote)
            ):
                large_fields = {
                    field
                    for field in QUOTE_FIELDS
                    if field.startswith("large_trade_")
                    or field.startswith("big_hand_")
                    or field.startswith("prev_big_hand_")
                }
                for field in large_fields:
                    quote_values.pop(field, None)
            quote.update(quote_values)
            return deepcopy(merged)

    def ensure_regular_close_snapshot(self, stock_code, snapshot):
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be a dict")
        code = self._normalized_code(stock_code)
        with self._lock:
            quote = self._ensure_quote(code)
            if quote.get("regular_close_snapshot_at"):
                return deepcopy(quote)
            timestamp_text = snapshot.get(
                "regular_close_snapshot_at"
            ) or self._kst_now().isoformat(timespec="seconds")
            values = {
                "regular_close_price": snapshot.get("regular_close_price"),
                "regular_close_change_rate": snapshot.get(
                    "regular_close_change_rate"
                ),
                "regular_close_ohlc": deepcopy(
                    snapshot.get("regular_close_ohlc")
                ),
                "regular_close_snapshot_at": timestamp_text,
                "regular_close_snapshot_source": snapshot.get(
                    "regular_close_snapshot_source"
                ),
                "regular_close_snapshot_status": snapshot.get(
                    "regular_close_snapshot_status"
                ) or "ok",
            }
            quote.update(
                {
                    key: value
                    for key, value in values.items()
                    if value is not None and value != ""
                }
            )
            self._sequence += 1
            quote["sequence"] = self._sequence
            self._updated_at = timestamp_text
            return deepcopy(quote)

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

    def _latest_snapshot_for_codes(self, stock_codes):
        return {
            "sequence": self._sequence,
            "updated_at": self._updated_at,
            "quotes": {
                code: deepcopy(self._quotes[code])
                for code in stock_codes
                if code in self._quotes
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

    def snapshot_latest(self):
        with self._lock:
            return self._latest_snapshot_for_codes(tuple(self._quotes))

    def snapshot_many(self, stock_codes):
        codes = tuple(dict.fromkeys(self._normalized_code(code) for code in stock_codes))
        with self._lock:
            return self._snapshot_for_codes(codes)

    def snapshot_latest_many(self, stock_codes):
        codes = tuple(dict.fromkeys(self._normalized_code(code) for code in stock_codes))
        with self._lock:
            return self._latest_snapshot_for_codes(codes)

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

    def snapshot_price_light(self, stock_codes=None, since_price_sequence=None):
        if since_price_sequence is not None:
            since_price_sequence = int(since_price_sequence)
            if since_price_sequence < 0:
                raise ValueError("since_price_sequence must be non-negative")
        if stock_codes is None:
            with self._lock:
                codes = tuple(self._quotes)
        else:
            codes = tuple(
                dict.fromkeys(self._normalized_code(code) for code in stock_codes)
            )
        with self._lock:
            if since_price_sequence is not None and since_price_sequence > self._sequence:
                raise ValueError("price_sequence_ahead")
            quotes = {}
            max_price_sequence = 0
            updated_at = None
            for code in codes:
                quote = self._quotes.get(code)
                if not quote:
                    continue
                price_sequence = quote.get("price_sequence") or 0
                try:
                    price_sequence = int(price_sequence)
                except (TypeError, ValueError):
                    price_sequence = 0
                max_price_sequence = max(max_price_sequence, price_sequence)
                if (
                    since_price_sequence is not None
                    and price_sequence <= since_price_sequence
                ):
                    continue
                quotes[code] = {
                    "stock_code": code,
                    "price": quote.get("price"),
                    "change_rate": quote.get("change_rate"),
                    "price_received_at": quote.get("price_received_at"),
                    "price_updated_at": quote.get("price_updated_at"),
                    "price_sequence": quote.get("price_sequence"),
                    "received_code": quote.get("received_code"),
                    "normalized_code": quote.get("normalized_code"),
                    "registered_code": quote.get("registered_code"),
                    "original_registered_code": quote.get(
                        "original_registered_code"
                    ),
                    "realtime_source_code": quote.get("realtime_source_code"),
                    "source_code": quote.get("source_code"),
                    "bid_volume": quote.get("bid_volume"),
                    "ask_volume": quote.get("ask_volume"),
                    "bid_ask_ratio": quote.get("bid_ask_ratio"),
                    "orderbook_received_at": quote.get("orderbook_received_at"),
                    "orderbook_updated_at": quote.get("orderbook_updated_at"),
                    "orderbook_sequence": quote.get("orderbook_sequence"),
                    "orderbook_source": quote.get("orderbook_source"),
                    "received_at": quote.get("received_at"),
                    "updated_at": quote.get("updated_at"),
                    "sequence": quote.get("sequence"),
                }
                timestamp = (
                    quote.get("price_updated_at")
                    or quote.get("price_received_at")
                    or quote.get("updated_at")
                    or quote.get("received_at")
                )
                if timestamp and (updated_at is None or str(timestamp) > str(updated_at)):
                    updated_at = timestamp
            return {
                "sequence": self._sequence,
                "price_sequence": max_price_sequence,
                "updated_at": updated_at or self._updated_at,
                "quotes": quotes,
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
                self._trade_windows.pop(code, None)
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
            self._trade_windows.clear()
            self._orderbook_events.clear()
            self._close_metric_snapshots.clear()
            self._close_metrics_persistent_snapshots.clear()
            self._load_persistent_close_metrics()
            self._last_seen.clear()
            self._base_ohlc.clear()
            self._sequence = 0
            self._updated_at = None
            self._store_update_guard_drop_count = 0
            self._older_trade_drop_count = 0
            self._latest_only_dropped_count = 0
            self._one_min_bucket_pruned_count = 0
            self._one_min_bucket_update_count = 0
            self._one_min_bucket_last_update_at = None


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
