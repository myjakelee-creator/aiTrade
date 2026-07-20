from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code, now_text, to_number

PATCH_VERSION = "s1_kiwoom_ws_0B_fid228_v1"
WS_SOURCE = "kiwoom_rest_ws_0B_fid228"
WS_STATUS_OK = "ok"


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _clock_minutes(text: Any, fallback: str) -> int:
    value = str(text or fallback).strip()
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def _iso_age_sec(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return max(0.0, (datetime.now() - parsed).total_seconds())


def _decode_message(message: Any) -> Any:
    if isinstance(message, bytes):
        message = message.decode("utf-8", errors="replace")
    if isinstance(message, str):
        text = message.strip()
        if not text:
            return message
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return message
    return message


def _is_ping(message: Any) -> bool:
    if isinstance(message, str):
        return message.strip().upper() == "PING"
    return isinstance(message, dict) and str(message.get("trnm") or "").upper() == "PING"


def _is_timeout(error: Exception) -> bool:
    name = error.__class__.__name__.lower()
    return "timeout" in name or isinstance(error, TimeoutError)


def parse_realtime_strength_message(message: Any) -> list[dict[str, Any]]:
    payload = _decode_message(message)
    if not isinstance(payload, dict) or str(payload.get("trnm") or "").upper() != "REAL":
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []

    result: list[dict[str, Any]] = []
    for entry in data:
        if not isinstance(entry, dict) or str(entry.get("type") or "").upper() != "0B":
            continue
        values = entry.get("values")
        if not isinstance(values, dict):
            continue
        strength = _number(values.get("228"))
        code = normalize_code(entry.get("item") or values.get("9001"))
        if not code or strength is None or strength <= 0:
            continue
        result.append(
            {
                "stock_code": code,
                "execution_strength": round(strength, 4),
                "execution_strength_source_time": str(values.get("20") or "").strip() or None,
                "execution_strength_exchange": str(values.get("9081") or "").strip() or None,
                "execution_strength_market_phase": str(values.get("290") or "").strip() or None,
                "execution_strength_trade_price": _number(values.get("10")),
                "raw_item": str(entry.get("item") or ""),
            }
        )
    return result


class _WebSocketConnection:
    def __init__(self, raw: Any, backend: str) -> None:
        self.raw = raw
        self.backend = backend

    def send(self, payload: Any) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.raw.send(text)

    def recv(self, timeout_sec: float) -> Any:
        if self.backend == "websockets.sync":
            return self.raw.recv(timeout=timeout_sec)
        self.raw.settimeout(timeout_sec)
        try:
            return self.raw.recv()
        except Exception as error:
            if _is_timeout(error):
                raise TimeoutError() from error
            raise

    def close(self) -> None:
        try:
            self.raw.close()
        except Exception:
            pass


def _connect_websocket(url: str, timeout_sec: float) -> _WebSocketConnection:
    try:
        from websockets.sync.client import connect

        raw = connect(
            url,
            open_timeout=timeout_sec,
            close_timeout=2,
            ping_interval=None,
        )
        return _WebSocketConnection(raw, "websockets.sync")
    except ImportError:
        pass

    try:
        import websocket
    except ImportError as error:
        raise RuntimeError(
            "realtime strength requires 'websockets' or 'websocket-client' in 64-bit Python"
        ) from error

    raw = websocket.create_connection(url, timeout=timeout_sec)
    return _WebSocketConnection(raw, "websocket-client")


def _session_phase(config: dict[str, Any]) -> str:
    now = datetime.now()
    minute = now.hour * 60 + now.minute
    regular = config.get("regular_session") or {}
    regular_start = _clock_minutes(regular.get("start"), "09:00")
    regular_end = _clock_minutes(regular.get("end"), "15:30")
    if regular_start <= minute < regular_end:
        return "regular"
    aftermarket = config.get("aftermarket_session") or {}
    aftermarket_start = _clock_minutes(aftermarket.get("start"), "15:30")
    aftermarket_end = _clock_minutes(aftermarket.get("end"), "20:00")
    if aftermarket_start <= minute < aftermarket_end:
        return "aftermarket"
    return "outside"


def _query_code(config: dict[str, Any], code: str, phase: str) -> str:
    mapping = config.get("query_suffix_by_session")
    suffix = mapping.get(phase) if isinstance(mapping, dict) else None
    if suffix in (None, ""):
        suffix = config.get("query_suffix") or ""
    return f"{code}{str(suffix).strip()}" if str(suffix).strip() else code


def _s1_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    code, row = item
    for key in ("candidate_rank", "model_rank", "pool_rank", "rank"):
        value = _number(row.get(key))
        if value is not None and value > 0:
            return (0, int(value), -(_number(row.get("trade_value_eok")) or 0.0), code)
    return (1, 999999, -(_number(row.get("trade_value_eok")) or 0.0), code)


def resolve_s1_code(state) -> tuple[str, str]:
    with state.lock:
        quotes = {
            normalize_code(code): dict(row)
            for code, row in state.quotes.items()
            if isinstance(row, dict) and normalize_code(code)
        }
    if not quotes:
        return "", "unresolved"

    candidate_rows = [
        (code, row)
        for code, row in quotes.items()
        if int(_number(row.get("candidate_rank")) or 0) == 1
        or int(_number(row.get("model_rank")) or 0) == 1
        or int(_number(row.get("rank")) or 0) == 1
    ]
    if candidate_rows:
        code, _row = min(candidate_rows, key=_s1_sort_key)
        return code, "canonical_rank1"

    code, _row = min(quotes.items(), key=_s1_sort_key)
    return code, "trade_value_top1"


class RealtimeStrengthWebSocket(threading.Thread):
    def __init__(self, state, config: dict[str, Any]) -> None:
        super().__init__(name="stockboard-realtime-strength-ws", daemon=True)
        self.state = state
        self.config = dict(config)
        ws_config = self.config.get("realtime_strength_ws")
        self.ws_config = dict(ws_config) if isinstance(ws_config, dict) else {}
        try:
            stage = int(self.config.get("rollout_stage") or 0)
            required_stage = int(self.ws_config.get("stage") or 99)
        except (TypeError, ValueError):
            stage, required_stage = 0, 99
        env_enabled = os.getenv("STOCKBOARD_REALTIME_STRENGTH_WS_ENABLED")
        enabled = bool(self.ws_config.get("enabled")) and stage >= required_stage
        if env_enabled is not None:
            enabled = str(env_enabled).strip().lower() in {"1", "true", "yes", "on"}
        self.enabled = enabled
        self.stop_event = threading.Event()
        self.connection_lock = threading.RLock()
        self.connection: _WebSocketConnection | None = None
        self.last_applied_mono = 0.0
        self.last_selected_check_mono = 0.0
        self.selected_code = ""
        self.selected_source = ""
        self.event_count = 0
        self.apply_count = 0
        self.changed_count = 0
        self.unchanged_count = 0
        self.reconnect_count = 0
        self._initialize_status()

    def _initialize_status(self) -> None:
        with self.state.lock:
            self.state.status.update(
                {
                    "realtime_strength_ws_installed": True,
                    "realtime_strength_ws_version": PATCH_VERSION,
                    "realtime_strength_ws_enabled": self.enabled,
                    "realtime_strength_ws_scope": "s1_only",
                    "realtime_strength_ws_status": "starting" if self.enabled else "disabled",
                    "realtime_strength_ws_source": WS_SOURCE,
                }
            )

    def _status(self, **values: Any) -> None:
        with self.state.lock:
            self.state.status.update(values)
            self.state.status["realtime_strength_ws_event_count"] = self.event_count
            self.state.status["realtime_strength_ws_apply_count"] = self.apply_count
            self.state.status["realtime_strength_ws_changed_count"] = self.changed_count
            self.state.status["realtime_strength_ws_unchanged_count"] = self.unchanged_count
            self.state.status["realtime_strength_ws_reconnect_count"] = self.reconnect_count

    def stop(self) -> None:
        self.stop_event.set()
        with self.connection_lock:
            connection = self.connection
        if connection is not None:
            connection.close()

    def _set_connection(self, connection: _WebSocketConnection | None) -> None:
        with self.connection_lock:
            self.connection = connection

    def _login(self, connection: _WebSocketConnection, timeout_sec: float) -> None:
        from kiwoom_data_provider import issue_access_token

        token = issue_access_token()
        connection.send({"trnm": "LOGIN", "token": token})
        deadline = time.monotonic() + max(5.0, timeout_sec)
        while time.monotonic() < deadline and not self.stop_event.is_set():
            try:
                message = _decode_message(connection.recv(min(2.0, timeout_sec)))
            except TimeoutError:
                continue
            if _is_ping(message):
                connection.send(message)
                continue
            if not isinstance(message, dict) or str(message.get("trnm") or "").upper() != "LOGIN":
                continue
            return_code = int(_number(message.get("return_code")) or 0)
            if return_code != 0:
                raise RuntimeError(f"WebSocket LOGIN failed: {message}")
            return
        raise TimeoutError("WebSocket LOGIN acknowledgement timed out")

    def _subscribe(self, connection: _WebSocketConnection, query_code: str) -> None:
        connection.send(
            {
                "trnm": "REG",
                "grp_no": str(self.ws_config.get("group_no") or "41"),
                "refresh": "0",
                "data": [{"item": [query_code], "type": ["0B"]}],
            }
        )

    def _selection_changed(self, code: str, phase: str) -> bool:
        refresh_sec = max(1.0, float(self.ws_config.get("selection_refresh_sec") or 5))
        now_mono = time.monotonic()
        if now_mono - self.last_selected_check_mono < refresh_sec:
            return False
        self.last_selected_check_mono = now_mono
        next_code, next_source = resolve_s1_code(self.state)
        next_phase = _session_phase(self.config)
        self.selected_source = next_source
        return next_code != code or next_phase != phase

    def _consume(
        self,
        connection: _WebSocketConnection,
        *,
        code: str,
        query_code: str,
        phase: str,
    ) -> None:
        receive_timeout = max(0.5, float(self.ws_config.get("receive_timeout_sec") or 2))
        apply_interval = max(0.2, float(self.ws_config.get("apply_interval_ms") or 1000) / 1000.0)
        self._status(
            realtime_strength_ws_status="subscribed",
            realtime_strength_ws_backend=connection.backend,
            realtime_strength_ws_selected_code=code,
            realtime_strength_ws_selected_source=self.selected_source,
            realtime_strength_ws_query_code=query_code,
            realtime_strength_ws_market_phase=phase,
            realtime_strength_ws_last_error=None,
            realtime_strength_ws_subscribed_at=now_text(),
        )

        while not self.stop_event.is_set():
            if self._selection_changed(code, phase):
                self._status(realtime_strength_ws_status="resubscribe_required")
                return
            try:
                raw_message = connection.recv(receive_timeout)
            except TimeoutError:
                continue
            message = _decode_message(raw_message)
            if _is_ping(message):
                connection.send(message)
                continue
            if isinstance(message, dict) and str(message.get("trnm") or "").upper() == "REG":
                return_code = int(_number(message.get("return_code")) or 0)
                if return_code != 0:
                    raise RuntimeError(f"WebSocket REG failed: {message}")
                continue

            events = parse_realtime_strength_message(message)
            for event in events:
                if event.get("stock_code") != code:
                    continue
                self.event_count += 1
                now_mono = time.monotonic()
                if now_mono - self.last_applied_mono < apply_interval:
                    continue
                self.last_applied_mono = now_mono
                changed = self.state.apply_realtime_strength_ws(event)
                self.apply_count += 1
                if changed:
                    self.changed_count += 1
                else:
                    self.unchanged_count += 1
                self._status(
                    realtime_strength_ws_status="ok",
                    realtime_strength_ws_last_event_at=now_text(),
                    realtime_strength_ws_last_source_time=event.get(
                        "execution_strength_source_time"
                    ),
                    realtime_strength_ws_last_value=event.get("execution_strength"),
                    realtime_strength_ws_last_error=None,
                )

    def run(self) -> None:
        if not self.enabled:
            self._status(realtime_strength_ws_status="disabled")
            return

        url = str(self.ws_config.get("url") or "").strip()
        connect_timeout = max(3.0, float(self.ws_config.get("connect_timeout_sec") or 10))
        backoff = max(3.0, float(self.ws_config.get("reconnect_backoff_sec") or 10))
        while not self.stop_event.is_set():
            phase = _session_phase(self.config)
            if phase == "outside":
                self._status(realtime_strength_ws_status="outside_active_session")
                self.stop_event.wait(2.0)
                continue
            code, source = resolve_s1_code(self.state)
            self.selected_code = code
            self.selected_source = source
            if not code:
                self._status(realtime_strength_ws_status="waiting_s1")
                self.stop_event.wait(1.0)
                continue
            query_code = _query_code(self.config, code, phase)
            connection: _WebSocketConnection | None = None
            try:
                self._status(
                    realtime_strength_ws_status="connecting",
                    realtime_strength_ws_selected_code=code,
                    realtime_strength_ws_selected_source=source,
                    realtime_strength_ws_query_code=query_code,
                    realtime_strength_ws_market_phase=phase,
                )
                connection = _connect_websocket(url, connect_timeout)
                self._set_connection(connection)
                self._login(connection, connect_timeout)
                self._subscribe(connection, query_code)
                self._consume(
                    connection,
                    code=code,
                    query_code=query_code,
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


def install(base) -> None:
    state_class = getattr(base, "State", None)
    updater_class = getattr(base, "ProgramNetUpdater", None)
    if state_class is None or updater_class is None:
        return
    if getattr(updater_class, "_stockboard_realtime_strength_ws_installed", False):
        return

    from realtime_v2.worker_rest_live_metrics_patch import _read_config

    original_state_init = state_class.__init__
    original_rows = state_class.rows

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["realtime_strength_ws_installed"] = True
            self.status["realtime_strength_ws_version"] = PATCH_VERSION

    def apply_realtime_strength_ws(self, event: dict[str, Any]) -> bool:
        code = normalize_code(event.get("stock_code"))
        strength = _number(event.get("execution_strength"))
        if not code or strength is None or strength <= 0:
            return False
        received_at = now_text()
        with self.lock:
            quote = self._quote(code)
            daily = self.daily_values_by_code.setdefault(code, {})
            previous = _number(quote.get("execution_strength"))
            previous_source = str(quote.get("execution_strength_source") or "")
            changed = previous_source != WS_SOURCE or previous != strength
            values = {
                "execution_strength": round(strength, 4),
                "execution_strength_source": WS_SOURCE,
                "execution_strength_status": WS_STATUS_OK,
                "execution_strength_updated_at": received_at,
                "execution_strength_received_at": received_at,
                "execution_strength_source_time": event.get(
                    "execution_strength_source_time"
                ),
                "execution_strength_exchange": event.get("execution_strength_exchange"),
                "execution_strength_market_phase": event.get(
                    "execution_strength_market_phase"
                ),
                "execution_strength_trade_price": event.get(
                    "execution_strength_trade_price"
                ),
                "last_valid_execution_strength": round(strength, 4),
                "last_valid_strength_at": received_at,
            }
            for target in (quote, daily):
                for key, value in values.items():
                    if value not in (None, ""):
                        target[key] = deepcopy(value)
            self.status["realtime_strength_ws_state_update_count"] = int(
                self.status.get("realtime_strength_ws_state_update_count") or 0
            ) + 1
            self.status["realtime_strength_ws_state_last_code"] = code
            self.status["realtime_strength_ws_state_last_at"] = received_at
            if changed:
                self._mark_daily_dirty()
        if changed:
            rebuild = getattr(self, "request_background_rebuild", None)
            if callable(rebuild):
                rebuild(reason="realtime_strength_ws", force=False)
        return changed

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        with self.lock:
            selected_code = normalize_code(
                self.status.get("realtime_strength_ws_selected_code")
            )
        config = _read_config()
        ws_config = config.get("realtime_strength_ws")
        ws_config = ws_config if isinstance(ws_config, dict) else {}
        stale_after = max(5.0, float(ws_config.get("stale_after_sec") or 20))
        available = 0
        hidden_legacy = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            code = normalize_code(row.get("stock_code"))
            source = str(row.get("execution_strength_source") or "")
            received_at = row.get("execution_strength_received_at") or row.get(
                "execution_strength_updated_at"
            )
            fresh = (
                code == selected_code
                and source == WS_SOURCE
                and (_iso_age_sec(received_at) or 0.0) <= stale_after
            )
            if fresh:
                row["execution_strength_available"] = True
                available += 1
                continue
            if row.get("execution_strength") not in (None, ""):
                row["execution_strength_legacy_snapshot"] = row.get(
                    "execution_strength"
                )
                row["execution_strength_legacy_source"] = source or "unknown"
                hidden_legacy += 1
            for key in (
                "execution_strength",
                "last_valid_execution_strength",
            ):
                row.pop(key, None)
            row["execution_strength_available"] = False
            row["execution_strength_status"] = (
                "stale_realtime_ws" if source == WS_SOURCE else "unavailable_not_realtime"
            )
        with self.lock:
            self.status["realtime_strength_ws_display_available_count"] = available
            self.status["realtime_strength_ws_hidden_legacy_count"] = hidden_legacy
        return result

    state_class.__init__ = state_init
    state_class.apply_realtime_strength_ws = apply_realtime_strength_ws
    state_class.rows = rows

    original_updater_class = updater_class

    class ProgramNetUpdaterWithRealtimeStrength(original_updater_class):
        _stockboard_realtime_strength_ws_installed = True

        def __init__(self, state, *args, **kwargs):
            super().__init__(state, *args, **kwargs)
            self.realtime_strength_ws = RealtimeStrengthWebSocket(
                state,
                _read_config(),
            )

        def start(self) -> None:
            super().start()
            self.realtime_strength_ws.start()

        def stop(self) -> None:
            self.realtime_strength_ws.stop()
            super().stop()

    base.ProgramNetUpdater = ProgramNetUpdaterWithRealtimeStrength
