from __future__ import annotations

import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "top20_kiwoom_ws_0B_fid228_batch_v1"


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _rank_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    code, row = item
    for key in ("candidate_rank", "model_rank", "pool_rank", "rank"):
        value = _number(row.get(key))
        if value is not None and value > 0:
            return (0, int(value), -(_number(row.get("trade_value_eok")) or 0.0), code)
    return (1, 999999, -(_number(row.get("trade_value_eok")) or 0.0), code)


def resolve_top_codes(state, limit: int = 20) -> list[str]:
    with state.lock:
        quotes = {
            normalize_code(code): dict(row)
            for code, row in state.quotes.items()
            if isinstance(row, dict) and normalize_code(code)
        }
    ranked = sorted(quotes.items(), key=_rank_key)
    return [code for code, _row in ranked[: max(1, min(20, int(limit)))]]


def _source_date() -> str:
    session = market_session_now(datetime.now())
    digits = "".join(
        character
        for character in str(session.trading_date or session.calendar_date or "")
        if character.isdigit()
    )
    return digits[:8] if len(digits) >= 8 else ""


def install(base) -> None:
    """Expand FID228 from S1 to Top20 with one connection and one batch per second.

    The price collector is untouched. All events received during a one-second window
    are coalesced by stock code, then the latest value for each code is committed under
    one state lock and one background-rebuild request.
    """

    import realtime_v2.worker_realtime_strength_ws_patch as module

    state_class = getattr(base, "State", None)
    updater_class = module.RealtimeStrengthWebSocket
    if state_class is None or getattr(updater_class, "_stockboard_top20_ws_installed", False):
        return

    original_state_init = state_class.__init__
    original_rows = state_class.rows
    original_initialize_status = updater_class._initialize_status

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["realtime_strength_ws_top20_installed"] = True
            self.status["realtime_strength_ws_top20_version"] = PATCH_VERSION

    def apply_batch(self, events: list[dict[str, Any]]) -> int:
        source_date = _source_date()
        changed_count = 0
        accepted_count = 0
        received_at = now_text()
        with self.lock:
            for event in events:
                if not isinstance(event, dict):
                    continue
                code = normalize_code(event.get("stock_code"))
                strength = _number(event.get("execution_strength"))
                if not code or strength is None or strength <= 0:
                    continue
                quote = self._quote(code)
                daily = self.daily_values_by_code.setdefault(code, {})
                previous = _number(quote.get("execution_strength"))
                previous_source = str(quote.get("execution_strength_source") or "")
                changed = previous_source != module.WS_SOURCE or previous != strength
                values = {
                    "execution_strength": round(strength, 4),
                    "execution_strength_source": module.WS_SOURCE,
                    "execution_strength_status": module.WS_STATUS_OK,
                    "execution_strength_updated_at": received_at,
                    "execution_strength_received_at": received_at,
                    "execution_strength_source_time": event.get(
                        "execution_strength_source_time"
                    ),
                    "execution_strength_exchange": event.get(
                        "execution_strength_exchange"
                    ),
                    "execution_strength_market_phase": event.get(
                        "execution_strength_market_phase"
                    ),
                    "execution_strength_trade_price": event.get(
                        "execution_strength_trade_price"
                    ),
                    "last_valid_execution_strength": round(strength, 4),
                    "last_valid_strength_at": received_at,
                    "execution_source_trading_date": source_date or None,
                    "_session_hold_execution_date": source_date or None,
                }
                for target in (quote, daily):
                    for key, value in values.items():
                        if value not in (None, ""):
                            target[key] = deepcopy(value)
                accepted_count += 1
                if changed:
                    changed_count += 1
            if accepted_count:
                self.status["realtime_strength_ws_batch_state_update_count"] = int(
                    self.status.get("realtime_strength_ws_batch_state_update_count") or 0
                ) + accepted_count
                self.status["realtime_strength_ws_batch_last_count"] = accepted_count
                self.status["realtime_strength_ws_batch_last_at"] = received_at
                self.status["execution_source_trading_date"] = source_date or None
                self._mark_daily_dirty()
        if changed_count:
            rebuild = getattr(self, "request_background_rebuild", None)
            if callable(rebuild):
                rebuild(reason="realtime_strength_ws_top20_batch", force=False)
        return changed_count

    def initialize_status(self) -> None:
        original_initialize_status(self)
        with self.state.lock:
            self.state.status["realtime_strength_ws_scope"] = "top20"
            self.state.status["realtime_strength_ws_top20_version"] = PATCH_VERSION

    def current_codes(self) -> list[str]:
        limit = int(self.ws_config.get("max_symbols") or 20)
        return resolve_top_codes(self.state, limit)

    def subscribe(self, connection, query_codes: list[str]) -> None:
        connection.send(
            {
                "trnm": "REG",
                "grp_no": str(self.ws_config.get("group_no") or "41"),
                "refresh": "0",
                "data": [{"item": list(query_codes), "type": ["0B"]}],
            }
        )

    def selection_changed(self, codes: tuple[str, ...], phase: str) -> bool:
        refresh_sec = max(1.0, float(self.ws_config.get("selection_refresh_sec") or 5))
        now_mono = time.monotonic()
        if now_mono - self.last_selected_check_mono < refresh_sec:
            return False
        self.last_selected_check_mono = now_mono
        next_codes = tuple(current_codes(self))
        next_phase = module._session_phase(self.config)
        return next_codes != codes or next_phase != phase

    def consume(self, connection, *, codes, query_codes, phase: str) -> None:
        code_set = set(codes)
        receive_timeout = max(0.5, float(self.ws_config.get("receive_timeout_sec") or 2))
        apply_interval = max(
            0.2,
            float(self.ws_config.get("apply_interval_ms") or 1000) / 1000.0,
        )
        pending: dict[str, dict[str, Any]] = {}
        last_flush = time.monotonic()

        def flush(force: bool = False) -> None:
            nonlocal last_flush
            if not pending:
                return
            now_mono = time.monotonic()
            if not force and now_mono - last_flush < apply_interval:
                return
            events = list(pending.values())
            pending.clear()
            last_flush = now_mono
            changed = self.state.apply_realtime_strength_ws_batch(events)
            self.apply_count += len(events)
            self.changed_count += int(changed)
            self.unchanged_count += max(0, len(events) - int(changed))
            latest = events[-1]
            self._status(
                realtime_strength_ws_status="ok",
                realtime_strength_ws_last_event_at=now_text(),
                realtime_strength_ws_last_source_time=latest.get(
                    "execution_strength_source_time"
                ),
                realtime_strength_ws_last_value=latest.get("execution_strength"),
                realtime_strength_ws_last_batch_count=len(events),
                realtime_strength_ws_last_error=None,
            )

        self._status(
            realtime_strength_ws_status="subscribed",
            realtime_strength_ws_backend=connection.backend,
            realtime_strength_ws_selected_code=codes[0] if codes else None,
            realtime_strength_ws_selected_codes=list(codes),
            realtime_strength_ws_selected_count=len(codes),
            realtime_strength_ws_selected_source="canonical_top20",
            realtime_strength_ws_query_code=query_codes[0] if query_codes else None,
            realtime_strength_ws_query_codes=list(query_codes),
            realtime_strength_ws_market_phase=phase,
            realtime_strength_ws_last_error=None,
            realtime_strength_ws_subscribed_at=now_text(),
            realtime_strength_ws_coalesce_mode="latest_per_code_batch_1hz",
        )

        while not self.stop_event.is_set():
            if selection_changed(self, tuple(codes), phase):
                flush(force=True)
                self._status(realtime_strength_ws_status="resubscribe_required")
                return
            try:
                raw_message = connection.recv(receive_timeout)
            except TimeoutError:
                flush()
                continue
            message = module._decode_message(raw_message)
            if module._is_ping(message):
                connection.send(message)
                flush()
                continue
            if isinstance(message, dict) and str(message.get("trnm") or "").upper() == "REG":
                return_code = int(module._number(message.get("return_code")) or 0)
                if return_code != 0:
                    raise RuntimeError(f"WebSocket REG failed: {message}")
                flush()
                continue
            for event in module.parse_realtime_strength_message(message):
                code = normalize_code(event.get("stock_code"))
                if code not in code_set:
                    continue
                self.event_count += 1
                pending[code] = event
            flush()

    def run(self) -> None:
        if not self.enabled:
            self._status(realtime_strength_ws_status="disabled")
            return
        url = str(self.ws_config.get("url") or "").strip()
        connect_timeout = max(3.0, float(self.ws_config.get("connect_timeout_sec") or 10))
        backoff = max(3.0, float(self.ws_config.get("reconnect_backoff_sec") or 10))
        while not self.stop_event.is_set():
            phase = module._session_phase(self.config)
            if phase == "outside":
                self._status(realtime_strength_ws_status="outside_active_session")
                self.stop_event.wait(2.0)
                continue
            codes = current_codes(self)
            if not codes:
                self._status(realtime_strength_ws_status="waiting_top20")
                self.stop_event.wait(1.0)
                continue
            query_codes = [module._query_code(self.config, code, phase) for code in codes]
            connection = None
            try:
                self._status(
                    realtime_strength_ws_status="connecting",
                    realtime_strength_ws_selected_code=codes[0],
                    realtime_strength_ws_selected_codes=list(codes),
                    realtime_strength_ws_selected_count=len(codes),
                    realtime_strength_ws_selected_source="canonical_top20",
                    realtime_strength_ws_query_codes=list(query_codes),
                    realtime_strength_ws_market_phase=phase,
                )
                connection = module._connect_websocket(url, connect_timeout)
                self._set_connection(connection)
                self._login(connection, connect_timeout)
                subscribe(self, connection, query_codes)
                consume(
                    self,
                    connection,
                    codes=tuple(codes),
                    query_codes=tuple(query_codes),
                    phase=phase,
                )
            except Exception as error:
                self.reconnect_count += 1
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

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        with self.lock:
            selected_codes = {
                normalize_code(code)
                for code in self.status.get("realtime_strength_ws_selected_codes", [])
                if normalize_code(code)
            }
            source_rows = {
                code: {
                    **dict(self.daily_values_by_code.get(code) or {}),
                    **dict(self.quotes.get(code) or {}),
                }
                for code in selected_codes
            }
        config = module._read_config()
        ws_config = config.get("realtime_strength_ws")
        ws_config = ws_config if isinstance(ws_config, dict) else {}
        stale_after = max(5.0, float(ws_config.get("stale_after_sec") or 20))
        available = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            source = source_rows.get(code, {})
            received_at = source.get("execution_strength_received_at") or source.get(
                "execution_strength_updated_at"
            )
            fresh = (
                code in selected_codes
                and str(source.get("execution_strength_source") or "") == module.WS_SOURCE
                and module._iso_age_sec(received_at) is not None
                and module._iso_age_sec(received_at) <= stale_after
            )
            if not fresh:
                continue
            for key, value in source.items():
                if key.startswith("execution_") or key in {
                    "last_valid_execution_strength",
                    "last_valid_strength_at",
                    "_session_hold_execution_date",
                }:
                    if value not in (None, ""):
                        row[key] = deepcopy(value)
            row["execution_strength_available"] = True
            available += 1
        with self.lock:
            self.status["realtime_strength_ws_display_available_count"] = available
        return result

    state_class.__init__ = state_init
    state_class.apply_realtime_strength_ws_batch = apply_batch
    state_class.rows = rows
    updater_class._initialize_status = initialize_status
    updater_class._current_codes = current_codes
    updater_class._subscribe_top20 = subscribe
    updater_class._selection_changed_top20 = selection_changed
    updater_class._consume = consume
    updater_class.run = run
    updater_class._stockboard_top20_ws_installed = True
