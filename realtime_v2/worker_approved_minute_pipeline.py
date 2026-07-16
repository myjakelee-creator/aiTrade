from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "approved_minute_metric_pipeline_v1"
TRADE_TYPE = "0B"
ORDERBOOK_TYPE_DEFAULT = "0D"
EXECUTION_SOURCE = "kiwoom_rest_ws_0B_fid228"
ORDERBOOK_SOURCE = "kiwoom_rest_ws_0D_rotating"
LARGE_SOURCE = "kiwoom_rest_ws_0B_fid15"
MINUTE_VALUE_SOURCE = "qax_fid14_minute_delta"
LARGE_THRESHOLD_DEFAULT = 50_000_000


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if number is None else int(number)


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _source_date(now: datetime | None = None) -> str:
    session = market_session_now(now or datetime.now())
    return _date_digits(session.trading_date or session.calendar_date)


def _minute_number(value: Any = None) -> int:
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone()
            return int(parsed.timestamp() // 60)
        except (TypeError, ValueError):
            pass
    return int(time.time() // 60)


def parse_trade_events(message: Any) -> list[dict[str, Any]]:
    import realtime_v2.worker_realtime_strength_ws_patch as ws_module

    payload = ws_module._decode_message(message)
    if not isinstance(payload, dict) or str(payload.get("trnm") or "").upper() != "REAL":
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    events: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or str(entry.get("type") or "").upper() != TRADE_TYPE:
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        code = normalize_code(entry.get("item") or values.get("9001"))
        if not code:
            continue
        strength = _number(values.get("228"))
        price = _number(values.get("10"))
        signed_qty = _integer(values.get("15"))
        cumulative_volume = _integer(values.get("13"))
        cumulative_value = _number(values.get("14"))
        events.append(
            {
                "stock_code": code,
                "execution_strength": strength,
                "execution_strength_source_time": str(values.get("20") or "").strip() or None,
                "execution_strength_exchange": str(values.get("9081") or "").strip() or None,
                "execution_strength_market_phase": str(values.get("290") or "").strip() or None,
                "execution_strength_trade_price": None if price is None else abs(price),
                "signed_trade_qty": signed_qty,
                "cumulative_volume": cumulative_volume,
                "cumulative_trade_value_raw": cumulative_value,
                "raw_item": str(entry.get("item") or ""),
            }
        )
    return events


def parse_orderbook_events(message: Any, config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    import realtime_v2.worker_realtime_strength_ws_patch as ws_module

    settings = dict(config or {})
    type_code = str(settings.get("type") or ORDERBOOK_TYPE_DEFAULT).upper()
    total_ask_field = str(settings.get("total_ask_field") or "121")
    total_bid_field = str(settings.get("total_bid_field") or "125")
    best_ask_field = str(settings.get("best_ask_field") or "27")
    best_bid_field = str(settings.get("best_bid_field") or "28")

    payload = ws_module._decode_message(message)
    if not isinstance(payload, dict) or str(payload.get("trnm") or "").upper() != "REAL":
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    events: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or str(entry.get("type") or "").upper() != type_code:
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        code = normalize_code(entry.get("item") or values.get("9001"))
        ask_volume = _integer(values.get(total_ask_field))
        bid_volume = _integer(values.get(total_bid_field))
        if not code or ask_volume is None or bid_volume is None:
            continue
        ask_volume = abs(ask_volume)
        bid_volume = abs(bid_volume)
        if ask_volume + bid_volume <= 0:
            continue
        events.append(
            {
                "stock_code": code,
                "ask_volume": ask_volume,
                "bid_volume": bid_volume,
                "best_ask_price": abs(_number(values.get(best_ask_field)) or 0.0) or None,
                "best_bid_price": abs(_number(values.get(best_bid_field)) or 0.0) or None,
                "bid_ask_ratio": None if ask_volume <= 0 else round(bid_volume / ask_volume, 4),
                "bid_pct": round(bid_volume / (ask_volume + bid_volume) * 100),
                "ask_pct": round(ask_volume / (ask_volume + bid_volume) * 100),
                "raw_item": str(entry.get("item") or ""),
            }
        )
    return events


def _rank_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    code, row = item
    for key in ("candidate_rank", "model_rank", "pool_rank", "rank"):
        rank = _number(row.get(key))
        if rank is not None and rank > 0:
            return (0, int(rank), -(_number(row.get("trade_value_eok")) or 0.0), code)
    return (1, 999999, -(_number(row.get("trade_value_eok")) or 0.0), code)


def _top_codes(state, limit: int = 100) -> list[str]:
    with state.lock:
        quotes = {
            normalize_code(code): dict(row)
            for code, row in state.quotes.items()
            if isinstance(row, dict) and normalize_code(code)
        }
    ranked = sorted(quotes.items(), key=_rank_key)
    return [code for code, _row in ranked[: max(1, min(100, int(limit or 100)))]]


def _query_codes(config: dict[str, Any], codes: list[str], phase: str) -> list[str]:
    import realtime_v2.worker_realtime_strength_ws_patch as ws_module

    return [ws_module._query_code(config, code, phase) for code in codes]


def _store_values(state, code: str, values: dict[str, Any]) -> None:
    quote = state._quote(code)
    daily = state.daily_values_by_code.setdefault(code, {})
    for target in (quote, daily):
        for key, value in values.items():
            if value not in (None, ""):
                target[key] = deepcopy(value)


def install(base) -> None:
    """Install the approved minute-published auxiliary metric pipeline.

    The verified 32-bit price collector is untouched. One existing 64-bit Kiwoom
    WebSocket connection carries Top100 trade events and one rotating 20-symbol
    orderbook group. Raw trade events are counted before latest-value coalescing.
    UI-facing execution, orderbook, large-trade and one-minute trade-value fields are
    published once per minute. ka10046 values are staged and released in the completed
    20-symbol minute batch while last good current-day values remain visible.
    """

    import realtime_v2.worker_five_metric_display_policy as display_policy
    import realtime_v2.worker_realtime_strength_ws_patch as ws_module
    import realtime_v2.worker_realtime_strength_ws_top20_patch as scope_module

    state_class = getattr(base, "State", None)
    updater_class = ws_module.RealtimeStrengthWebSocket
    if state_class is None or getattr(state_class, "_stockboard_approved_minute_pipeline_installed", False):
        return

    persist_keys = (
        "ui_bid_ask_ratio",
        "ui_bid_pct",
        "ui_ask_pct",
        "ui_bid_volume",
        "ui_ask_volume",
        "ui_best_ask_price",
        "ui_best_bid_price",
        "ui_orderbook_observed_at",
        "ui_orderbook_source_trading_date",
        "ui_execution_strength",
        "ui_execution_strength_observed_at",
        "ui_execution_source_trading_date",
        "ui_strength_5m",
        "ui_strength_20m",
        "ui_strength_60m",
        "ui_strength_observed_at",
        "ui_strength_source_trading_date",
        "ui_large_trade_buy_count",
        "ui_large_trade_sell_count",
        "ui_large_trade_net_count",
        "ui_large_trade_buy_sum_eok",
        "ui_large_trade_sell_sum_eok",
        "ui_large_trade_net_sum_eok",
        "ui_large_trade_quality",
        "ui_large_trade_observed_at",
        "ui_large_trade_source_trading_date",
        "trade_value_1m_eok",
        "trade_value_prev_1m_eok",
        "trade_value_1m_ratio_pct",
        "trade_value_1m_quality",
        "trade_value_1m_observed_at",
        "trade_value_1m_source_trading_date",
    )
    base.DAILY_PERSIST_KEYS = tuple(dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *persist_keys)))

    orderbook_policy = display_policy.POLICIES.get("orderbook")
    if isinstance(orderbook_policy, dict):
        orderbook_policy["max_age"] = lambda _row: 86_400.0
        orderbook_policy["active_basis"] = "rotating_orderbook_60s_last_good"
    execution_policy = display_policy.POLICIES.get("execution")
    if isinstance(execution_policy, dict):
        execution_policy["max_age"] = lambda _row: 86_400.0
        execution_policy["active_basis"] = "fid228_60s_published_last_good"

    original_state_init = state_class.__init__
    original_rows = state_class.rows
    original_apply_trade = state_class._apply_trade
    original_apply_rest = getattr(state_class, "apply_rest_live_metric_values", None)

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        self._approved_execution_stage: dict[str, dict[str, Any]] = {}
        self._approved_orderbook_stage: dict[str, dict[str, Any]] = {}
        self._approved_strength_stage: dict[str, dict[str, Any]] = {}
        self._approved_large_live: dict[str, dict[str, Any]] = {}
        self._approved_large_seen: dict[str, set[str]] = {}
        self._approved_trade_value_last: dict[str, float] = {}
        self._approved_trade_value_buckets: dict[str, dict[int, float]] = {}
        self._approved_trade_value_partial: set[str] = set()
        self._approved_last_publish_minute = _minute_number()
        with self.lock:
            for code, daily in self.daily_values_by_code.items():
                if not isinstance(daily, dict):
                    continue
                self._approved_large_live[code] = {
                    "buy_count": int(_number(daily.get("ui_large_trade_buy_count")) or 0),
                    "sell_count": int(_number(daily.get("ui_large_trade_sell_count")) or 0),
                    "buy_sum_eok": float(_number(daily.get("ui_large_trade_buy_sum_eok")) or 0.0),
                    "sell_sum_eok": float(_number(daily.get("ui_large_trade_sell_sum_eok")) or 0.0),
                    "quality": str(daily.get("ui_large_trade_quality") or "EXACT_LIVE"),
                }
            self.status["approved_minute_pipeline_installed"] = True
            self.status["approved_minute_pipeline_version"] = PATCH_VERSION
            self.status["approved_ui_publish_interval_sec"] = 60
            self.status["approved_strength_batch_size_per_minute"] = 20
            self.status["approved_orderbook_rotation_symbols"] = 20
            self.status["approved_orderbook_rotation_sec"] = 12

    def apply_trade(self, event: dict[str, Any]) -> None:
        original_apply_trade(self, event)
        values = base.merged_event_values(event)
        code = normalize_code(
            event.get("stock_code")
            or event.get("received_code")
            or values.get("stock_code")
            or values.get("normalized_code")
            or values.get("received_code")
        )
        if not code:
            return
        with self.lock:
            quote = self.quotes.get(code) or {}
            cumulative = _number(quote.get("trade_value_eok"))
            if cumulative is None:
                return
            previous = self._approved_trade_value_last.get(code)
            self._approved_trade_value_last[code] = cumulative
            if previous is None or cumulative < previous:
                self._approved_trade_value_partial.add(code)
                return
            delta = max(0.0, cumulative - previous)
            minute = _minute_number(event.get("ts") or quote.get("received_at"))
            buckets = self._approved_trade_value_buckets.setdefault(code, {})
            buckets[minute] = round(float(buckets.get(minute, 0.0)) + delta, 6)
            for old_minute in tuple(buckets):
                if old_minute < minute - 4:
                    buckets.pop(old_minute, None)

    def apply_rest_live_metric_values(self, raw_code, values, metric):
        if not callable(original_apply_rest):
            return 0
        updated = original_apply_rest(self, raw_code, values, metric)
        if not updated or metric != "strength":
            return updated
        code = normalize_code(raw_code)
        if not code:
            return updated
        observed_at = now_text()
        source_date = _source_date()
        with self.lock:
            quote = self.quotes.get(code) or {}
            staged = {
                "strength_5m": _number(quote.get("strength_5m")),
                "strength_20m": _number(quote.get("strength_20m")),
                "strength_60m": _number(quote.get("strength_60m")),
                "strength_source": str(quote.get("strength_source") or "ka10046_rest_lowload"),
                "strength_status": str(quote.get("strength_status") or "ok"),
                "strength_snapshot_at": quote.get("strength_snapshot_at") or observed_at,
                "strength_source_trading_date": _date_digits(
                    quote.get("strength_source_trading_date") or source_date
                ),
            }
            if staged["strength_5m"] is not None:
                self._approved_strength_stage[code] = staged
            daily = self.daily_values_by_code.setdefault(code, {})
            for target in (quote, daily):
                published = target.get("ui_strength_5m")
                if published is None:
                    target.pop("strength_5m", None)
                    target.pop("strength_20m", None)
                    target.pop("strength_60m", None)
                else:
                    target["strength_5m"] = published
                    if target.get("ui_strength_20m") is not None:
                        target["strength_20m"] = target.get("ui_strength_20m")
                    if target.get("ui_strength_60m") is not None:
                        target["strength_60m"] = target.get("ui_strength_60m")
        return updated

    def stage_trade_events(self, events: list[dict[str, Any]]) -> None:
        observed_at = now_text()
        source_date = _source_date()
        threshold = LARGE_THRESHOLD_DEFAULT
        try:
            config = ws_module._read_config()
            threshold = int(
                ((config.get("metrics") or {}).get("large_trade") or {}).get("threshold_krw")
                or threshold
            )
        except Exception:
            pass
        with self.lock:
            for event in events:
                if not isinstance(event, dict):
                    continue
                code = normalize_code(event.get("stock_code"))
                if not code:
                    continue
                strength = _number(event.get("execution_strength"))
                if strength is not None and strength > 0:
                    self._approved_execution_stage[code] = {
                        "execution_strength": round(strength, 4),
                        "execution_strength_source": EXECUTION_SOURCE,
                        "execution_strength_status": "ok",
                        "execution_strength_received_at": observed_at,
                        "execution_strength_updated_at": observed_at,
                        "execution_strength_source_time": event.get("execution_strength_source_time"),
                        "execution_strength_exchange": event.get("execution_strength_exchange"),
                        "execution_strength_market_phase": event.get("execution_strength_market_phase"),
                        "execution_strength_trade_price": event.get("execution_strength_trade_price"),
                        "execution_source_trading_date": source_date,
                    }
                price = _number(event.get("execution_strength_trade_price"))
                signed_qty = _integer(event.get("signed_trade_qty"))
                if price is None or signed_qty in (None, 0):
                    continue
                cumulative_volume = event.get("cumulative_volume")
                event_key = "|".join(
                    str(part or "")
                    for part in (
                        event.get("execution_strength_source_time"),
                        price,
                        signed_qty,
                        cumulative_volume,
                        event.get("execution_strength_exchange"),
                    )
                )
                seen = self._approved_large_seen.setdefault(code, set())
                if event_key in seen:
                    continue
                seen.add(event_key)
                if len(seen) > 20_000:
                    self._approved_large_seen[code] = set(list(seen)[-10_000:])
                amount_krw = abs(price * signed_qty)
                if amount_krw < threshold:
                    continue
                live = self._approved_large_live.setdefault(
                    code,
                    {
                        "buy_count": 0,
                        "sell_count": 0,
                        "buy_sum_eok": 0.0,
                        "sell_sum_eok": 0.0,
                        "quality": "EXACT_LIVE",
                    },
                )
                amount_eok = amount_krw / 100_000_000
                if signed_qty > 0:
                    live["buy_count"] = int(live.get("buy_count") or 0) + 1
                    live["buy_sum_eok"] = round(float(live.get("buy_sum_eok") or 0.0) + amount_eok, 4)
                else:
                    live["sell_count"] = int(live.get("sell_count") or 0) + 1
                    live["sell_sum_eok"] = round(float(live.get("sell_sum_eok") or 0.0) + amount_eok, 4)
                live["quality"] = str(live.get("quality") or "EXACT_LIVE")
                live["observed_at"] = observed_at
                live["source_date"] = source_date
            self.status["approved_trade_raw_event_count"] = int(
                self.status.get("approved_trade_raw_event_count") or 0
            ) + len(events)

    def stage_orderbook_events(self, events: list[dict[str, Any]]) -> None:
        observed_at = now_text()
        source_date = _source_date()
        with self.lock:
            for event in events:
                code = normalize_code(event.get("stock_code")) if isinstance(event, dict) else ""
                if not code:
                    continue
                staged = dict(event)
                staged["orderbook_received_at"] = observed_at
                staged["orderbook_source_trading_date"] = source_date
                self._approved_orderbook_stage[code] = staged
            self.status["approved_orderbook_raw_event_count"] = int(
                self.status.get("approved_orderbook_raw_event_count") or 0
            ) + len(events)
            if events:
                self.status["approved_orderbook_last_event_at"] = observed_at

    def mark_stream_disconnect(self) -> None:
        with self.lock:
            for live in self._approved_large_live.values():
                if isinstance(live, dict) and live.get("quality") == "EXACT_LIVE":
                    live["quality"] = "GAP_POSSIBLE"
            self.status["approved_stream_quality"] = "RECONNECT_HOLD"
            self.status["approved_stream_disconnected_at"] = now_text()

    def publish_minute(self, force: bool = False) -> bool:
        current_minute = _minute_number()
        if not force and current_minute <= self._approved_last_publish_minute:
            return False
        completed_minute = current_minute - 1
        previous_minute = current_minute - 2
        source_date = _source_date()
        published_at = now_text()
        changed = False
        with self.lock:
            for code, staged in self._approved_execution_stage.items():
                values = {
                    "ui_execution_strength": staged.get("execution_strength"),
                    "ui_execution_strength_observed_at": staged.get("execution_strength_received_at") or published_at,
                    "ui_execution_source_trading_date": staged.get("execution_source_trading_date") or source_date,
                    **staged,
                }
                _store_values(self, code, values)
                changed = True
            for code, staged in self._approved_orderbook_stage.items():
                values = {
                    "ui_bid_ask_ratio": staged.get("bid_ask_ratio"),
                    "ui_bid_pct": staged.get("bid_pct"),
                    "ui_ask_pct": staged.get("ask_pct"),
                    "ui_bid_volume": staged.get("bid_volume"),
                    "ui_ask_volume": staged.get("ask_volume"),
                    "ui_best_ask_price": staged.get("best_ask_price"),
                    "ui_best_bid_price": staged.get("best_bid_price"),
                    "ui_orderbook_observed_at": staged.get("orderbook_received_at") or published_at,
                    "ui_orderbook_source_trading_date": staged.get("orderbook_source_trading_date") or source_date,
                    "orderbook_source": ORDERBOOK_SOURCE,
                    "orderbook_status": "current_session_rotating_snapshot",
                }
                _store_values(self, code, values)
                changed = True
            for code, staged in tuple(self._approved_strength_stage.items()):
                values = {
                    "ui_strength_5m": staged.get("strength_5m"),
                    "ui_strength_20m": staged.get("strength_20m"),
                    "ui_strength_60m": staged.get("strength_60m"),
                    "ui_strength_observed_at": staged.get("strength_snapshot_at") or published_at,
                    "ui_strength_source_trading_date": staged.get("strength_source_trading_date") or source_date,
                    **staged,
                }
                _store_values(self, code, values)
                changed = True
            self._approved_strength_stage.clear()
            for code, live in self._approved_large_live.items():
                buy_count = int(live.get("buy_count") or 0)
                sell_count = int(live.get("sell_count") or 0)
                buy_sum = float(live.get("buy_sum_eok") or 0.0)
                sell_sum = float(live.get("sell_sum_eok") or 0.0)
                observed_at = live.get("observed_at") or published_at
                values = {
                    "ui_large_trade_buy_count": buy_count,
                    "ui_large_trade_sell_count": sell_count,
                    "ui_large_trade_net_count": buy_count - sell_count,
                    "ui_large_trade_buy_sum_eok": round(buy_sum, 4),
                    "ui_large_trade_sell_sum_eok": round(sell_sum, 4),
                    "ui_large_trade_net_sum_eok": round(buy_sum - sell_sum, 4),
                    "ui_large_trade_quality": str(live.get("quality") or "EXACT_LIVE"),
                    "ui_large_trade_observed_at": observed_at,
                    "ui_large_trade_source_trading_date": live.get("source_date") or source_date,
                    "large_trade_source": LARGE_SOURCE,
                    "large_trade_status": str(live.get("quality") or "EXACT_LIVE"),
                }
                _store_values(self, code, values)
                changed = True
            codes = set(self._approved_trade_value_buckets) | set(self.quotes)
            for code in codes:
                buckets = self._approved_trade_value_buckets.get(code, {})
                current_value = round(float(buckets.get(completed_minute, 0.0)), 4)
                previous_value = round(float(buckets.get(previous_minute, 0.0)), 4)
                ratio = None
                if previous_value > 0:
                    ratio = round(current_value / previous_value * 100, 1)
                elif current_value > 0:
                    ratio = 999.9
                quality = "PARTIAL_AFTER_RESTART" if code in self._approved_trade_value_partial else "COMPLETE_MINUTE"
                values = {
                    "trade_value_1m_eok": current_value,
                    "trade_value_prev_1m_eok": previous_value,
                    "trade_value_1m_ratio_pct": ratio,
                    "trade_value_1m_quality": quality,
                    "trade_value_1m_observed_at": published_at,
                    "trade_value_1m_source_trading_date": source_date,
                    "trade_value_1m_source": MINUTE_VALUE_SOURCE,
                }
                _store_values(self, code, values)
                changed = True
            self._approved_trade_value_partial.clear()
            self._approved_last_publish_minute = current_minute
            self.status["approved_minute_last_published_at"] = published_at
            self.status["approved_minute_last_published_minute"] = current_minute
            self.status["approved_execution_published_count"] = len(self._approved_execution_stage)
            self.status["approved_orderbook_published_count"] = len(self._approved_orderbook_stage)
            self.status["approved_strength_published_count"] = len(self._approved_strength_stage)
            self.status["approved_large_trade_published_count"] = len(self._approved_large_live)
            self.status["approved_stream_quality"] = "LIVE"
            if changed:
                self._mark_daily_dirty()
        if changed:
            rebuild = getattr(self, "request_background_rebuild", None)
            if callable(rebuild):
                rebuild(reason="approved_minute_publish", force=False)
        return changed

    def rows(self, limit: int = 300):
        publish_minute(self)
        result = original_rows(self, limit)
        now = datetime.now()
        import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

        session = market_session_now(now)
        expected = lifecycle._expected_date(session, now)
        with self.lock:
            source_rows = {
                normalize_code(code): {
                    **dict(self.daily_values_by_code.get(normalize_code(code)) or {}),
                    **dict(self.quotes.get(normalize_code(code)) or {}),
                }
                for code in {normalize_code(row.get("stock_code")) for row in result if isinstance(row, dict)}
                if code
            }
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            source = source_rows.get(code, {})
            if not source:
                continue
            orderbook_date = _date_digits(source.get("ui_orderbook_source_trading_date"))
            if expected and orderbook_date == expected and source.get("ui_bid_ask_ratio") is not None:
                row.update(
                    {
                        "bid_ask_ratio": source.get("ui_bid_ask_ratio"),
                        "bid_pct": source.get("ui_bid_pct"),
                        "ask_pct": source.get("ui_ask_pct"),
                        "bid_volume": source.get("ui_bid_volume"),
                        "ask_volume": source.get("ui_ask_volume"),
                        "best_ask_price": source.get("ui_best_ask_price"),
                        "best_bid_price": source.get("ui_best_bid_price"),
                        "orderbook_received_at": source.get("ui_orderbook_observed_at"),
                        "orderbook_source_trading_date": orderbook_date,
                        "orderbook_source": ORDERBOOK_SOURCE,
                        "orderbook_status": "published_60s_last_good",
                        "orderbook_available": True,
                    }
                )
            execution_date = _date_digits(source.get("ui_execution_source_trading_date"))
            if expected and execution_date == expected and source.get("ui_execution_strength") is not None:
                row.update(
                    {
                        "execution_strength": source.get("ui_execution_strength"),
                        "execution_strength_received_at": source.get("ui_execution_strength_observed_at"),
                        "execution_source_trading_date": execution_date,
                        "execution_strength_source": EXECUTION_SOURCE,
                        "execution_strength_status": "published_60s_last_good",
                        "execution_strength_available": True,
                    }
                )
            strength_date = _date_digits(source.get("ui_strength_source_trading_date"))
            if expected and strength_date == expected and source.get("ui_strength_5m") is not None:
                row.update(
                    {
                        "strength_5m": source.get("ui_strength_5m"),
                        "strength_20m": source.get("ui_strength_20m"),
                        "strength_60m": source.get("ui_strength_60m"),
                        "strength_snapshot_at": source.get("ui_strength_observed_at"),
                        "strength_source_trading_date": strength_date,
                        "strength_source": "ka10046_rest_lowload",
                        "strength_status": "published_minute_batch_last_good",
                        "strength5_available": True,
                    }
                )
            large_date = _date_digits(source.get("ui_large_trade_source_trading_date"))
            if expected and large_date == expected and source.get("ui_large_trade_net_count") is not None:
                row.update(
                    {
                        "large_trade_buy_count": source.get("ui_large_trade_buy_count"),
                        "large_trade_sell_count": source.get("ui_large_trade_sell_count"),
                        "large_trade_net_count": source.get("ui_large_trade_net_count"),
                        "large_trade_buy_sum_eok": source.get("ui_large_trade_buy_sum_eok"),
                        "large_trade_sell_sum_eok": source.get("ui_large_trade_sell_sum_eok"),
                        "large_trade_net_sum_eok": source.get("ui_large_trade_net_sum_eok"),
                        "large_trade_quality": source.get("ui_large_trade_quality"),
                        "large_trade_updated_at": source.get("ui_large_trade_observed_at"),
                        "large_trade_source_trading_date": large_date,
                        "large_trade_source": LARGE_SOURCE,
                        "large_trade_status": source.get("ui_large_trade_quality"),
                        "large_trade_available": True,
                    }
                )
            minute_date = _date_digits(source.get("trade_value_1m_source_trading_date"))
            if expected and minute_date == expected:
                for key in (
                    "trade_value_1m_eok",
                    "trade_value_prev_1m_eok",
                    "trade_value_1m_ratio_pct",
                    "trade_value_1m_quality",
                    "trade_value_1m_observed_at",
                    "trade_value_1m_source_trading_date",
                ):
                    if key in source:
                        row[key] = deepcopy(source.get(key))
        return result

    def integrated_run(self) -> None:
        if not self.enabled:
            self._status(realtime_strength_ws_status="disabled")
            return
        url = str(self.ws_config.get("url") or "").strip()
        connect_timeout = max(3.0, float(self.ws_config.get("connect_timeout_sec") or 10))
        backoff = max(3.0, float(self.ws_config.get("reconnect_backoff_sec") or 10))
        receive_timeout = min(1.0, max(0.2, float(self.ws_config.get("receive_timeout_sec") or 2)))
        full_config = dict(self.config)
        orderbook_config = full_config.get("realtime_orderbook_ws")
        orderbook_config = dict(orderbook_config) if isinstance(orderbook_config, dict) else {}
        orderbook_enabled = bool(orderbook_config.get("enabled", True))
        rotation_size = max(1, min(20, int(orderbook_config.get("rotation_symbols") or 20)))
        rotation_sec = max(5.0, float(orderbook_config.get("rotation_sec") or 12))
        strength_group = str(self.ws_config.get("group_no") or "41")
        orderbook_group = str(orderbook_config.get("group_no") or "42")
        orderbook_type = str(orderbook_config.get("type") or ORDERBOOK_TYPE_DEFAULT)

        while not self.stop_event.is_set():
            phase = ws_module._session_phase(full_config)
            if phase == "outside":
                self._status(realtime_strength_ws_status="outside_active_session")
                self.stop_event.wait(2.0)
                continue
            codes = _top_codes(self.state, int(self.ws_config.get("max_symbols") or 100))
            if not codes:
                self._status(realtime_strength_ws_status="waiting_top100")
                self.stop_event.wait(1.0)
                continue
            connection = None
            try:
                connection = ws_module._connect_websocket(url, connect_timeout)
                self._set_connection(connection)
                self._login(connection, connect_timeout)
                query_codes = _query_codes(full_config, codes, phase)
                connection.send(
                    {
                        "trnm": "REG",
                        "grp_no": strength_group,
                        "refresh": "1",
                        "data": [{"item": query_codes, "type": [TRADE_TYPE]}],
                    }
                )
                rotation_index = 0
                last_rotation = 0.0
                last_code_refresh = time.monotonic()
                if orderbook_enabled:
                    batch = codes[:rotation_size]
                    connection.send(
                        {
                            "trnm": "REG",
                            "grp_no": orderbook_group,
                            "refresh": "1",
                            "data": [
                                {
                                    "item": _query_codes(full_config, batch, phase),
                                    "type": [orderbook_type],
                                }
                            ],
                        }
                    )
                    last_rotation = time.monotonic()
                self._status(
                    realtime_strength_ws_status="subscribed",
                    realtime_strength_ws_backend=connection.backend,
                    realtime_strength_ws_selected_codes=list(codes),
                    realtime_strength_ws_selected_count=len(codes),
                    realtime_strength_ws_scope="top100",
                    realtime_strength_ws_market_phase=phase,
                    approved_orderbook_ws_enabled=orderbook_enabled,
                    approved_orderbook_ws_type=orderbook_type,
                    approved_orderbook_rotation_index=rotation_index,
                    approved_orderbook_rotation_codes=codes[:rotation_size],
                    approved_stream_connected_at=now_text(),
                    realtime_strength_ws_last_error=None,
                )
                while not self.stop_event.is_set():
                    now_mono = time.monotonic()
                    next_phase = ws_module._session_phase(full_config)
                    if next_phase != phase:
                        break
                    if now_mono - last_code_refresh >= 60.0:
                        next_codes = _top_codes(self.state, int(self.ws_config.get("max_symbols") or 100))
                        if next_codes != codes:
                            codes = next_codes
                            query_codes = _query_codes(full_config, codes, phase)
                            connection.send(
                                {
                                    "trnm": "REG",
                                    "grp_no": strength_group,
                                    "refresh": "1",
                                    "data": [{"item": query_codes, "type": [TRADE_TYPE]}],
                                }
                            )
                        last_code_refresh = now_mono
                    if orderbook_enabled and now_mono - last_rotation >= rotation_sec:
                        rotation_index = (rotation_index + 1) % max(1, (len(codes) + rotation_size - 1) // rotation_size)
                        start = rotation_index * rotation_size
                        batch = codes[start : start + rotation_size]
                        if len(batch) < rotation_size:
                            batch += codes[: rotation_size - len(batch)]
                        connection.send(
                            {
                                "trnm": "REG",
                                "grp_no": orderbook_group,
                                "refresh": "1",
                                "data": [
                                    {
                                        "item": _query_codes(full_config, batch, phase),
                                        "type": [orderbook_type],
                                    }
                                ],
                            }
                        )
                        last_rotation = now_mono
                        self._status(
                            approved_orderbook_rotation_index=rotation_index,
                            approved_orderbook_rotation_codes=list(batch),
                            approved_orderbook_rotation_at=now_text(),
                        )
                    try:
                        raw_message = connection.recv(receive_timeout)
                    except TimeoutError:
                        continue
                    message = ws_module._decode_message(raw_message)
                    if ws_module._is_ping(message):
                        connection.send(message)
                        continue
                    if isinstance(message, dict) and str(message.get("trnm") or "").upper() == "REG":
                        return_code = int(ws_module._number(message.get("return_code")) or 0)
                        if return_code != 0:
                            raise RuntimeError(f"WebSocket REG failed: {message}")
                        continue
                    trade_events = parse_trade_events(message)
                    if trade_events:
                        self.event_count += len(trade_events)
                        self.state.stage_approved_trade_events(trade_events)
                        self.apply_count += len(trade_events)
                        latest = trade_events[-1]
                        self._status(
                            realtime_strength_ws_status="ok",
                            realtime_strength_ws_last_event_at=now_text(),
                            realtime_strength_ws_last_source_time=latest.get(
                                "execution_strength_source_time"
                            ),
                            realtime_strength_ws_last_value=latest.get("execution_strength"),
                            realtime_strength_ws_last_error=None,
                        )
                    orderbook_events = parse_orderbook_events(message, orderbook_config)
                    if orderbook_events:
                        self.state.stage_approved_orderbook_events(orderbook_events)
            except Exception as error:
                self.reconnect_count += 1
                self.state.mark_approved_stream_disconnect()
                self._status(
                    realtime_strength_ws_status="error_backoff",
                    realtime_strength_ws_last_error=f"{type(error).__name__}: {error}",
                    realtime_strength_ws_last_error_at=now_text(),
                )
                self.stop_event.wait(backoff)
            finally:
                self._set_connection(None)
                if connection is not None:
                    connection.close()

    state_class.__init__ = state_init
    state_class._apply_trade = apply_trade
    if callable(original_apply_rest):
        state_class.apply_rest_live_metric_values = apply_rest_live_metric_values
    state_class.stage_approved_trade_events = stage_trade_events
    state_class.stage_approved_orderbook_events = stage_orderbook_events
    state_class.mark_approved_stream_disconnect = mark_stream_disconnect
    state_class.publish_approved_minute_metrics = publish_minute
    state_class.rows = rows
    updater_class.run = integrated_run
    scope_module.resolve_top_codes = _top_codes
    state_class._stockboard_approved_minute_pipeline_installed = True
