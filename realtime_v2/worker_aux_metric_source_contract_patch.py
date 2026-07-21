from __future__ import annotations

"""Correct StockBoard auxiliary metric source contracts without adding load.

The production price collector remains untouched.  The existing single Kiwoom
WebSocket connection and existing single-flight REST owner are reused; only the
wrong realtime/TR identifiers and the accepted display-source contracts are
corrected.
"""

import time
from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, now_text
from realtime_v2.tr_singleflight import get_shared_tr_coordinator

PATCH_VERSION = "aux_metric_source_contract_v1"
EXECUTION_REAL_TYPE = "0A"       # 주식체결
ORDERBOOK_REAL_TYPE = "0C"       # 주식호가잔량
STRENGTH_TREND_API_ID = "ka10045"  # 체결강도추이시간별
PROGRAM_API_ID = "ka90003"       # 종목별프로그램매매현황
EXECUTION_SOURCE = "kiwoom_rest_ws_0A_fid228"
STRENGTH_SOURCE = "ka10045_rest_lowload"
PROGRAM_SOURCE = "ka90003_tr_singleflight"


def _corrected_config(original_reader):
    def read_config() -> dict[str, Any]:
        raw = original_reader()
        config = deepcopy(raw) if isinstance(raw, dict) else {}

        strength_ws = config.setdefault("realtime_strength_ws", {})
        strength_ws["type"] = EXECUTION_REAL_TYPE
        strength_ws["strength_field"] = "228"

        orderbook_ws = config.setdefault("realtime_orderbook_ws", {})
        orderbook_ws["type"] = ORDERBOOK_REAL_TYPE

        metrics = config.setdefault("metrics", {})
        strength = metrics.setdefault("strength", {})
        strength["api_id"] = STRENGTH_TREND_API_ID
        strength["purpose"] = "5분·20분·60분 장중 체결강도 추이 전용"
        return config

    return read_config


def _install_execution_contract(base) -> None:
    import realtime_v2.worker_realtime_strength_ws_patch as ws

    updater_class = ws.RealtimeStrengthWebSocket
    if getattr(updater_class, "_stockboard_execution_source_contract_installed", False):
        return

    ws.PATCH_VERSION = "kiwoom_ws_0A_fid228_v2"
    ws.WS_SOURCE = EXECUTION_SOURCE
    ws.WS_REAL_TYPE = EXECUTION_REAL_TYPE

    def parse_realtime_strength_message(message: Any) -> list[dict[str, Any]]:
        payload = ws._decode_message(message)
        if not isinstance(payload, dict) or str(payload.get("trnm") or "").upper() != "REAL":
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        result: list[dict[str, Any]] = []
        for entry in data:
            if (
                not isinstance(entry, dict)
                or str(entry.get("type") or "").upper() != EXECUTION_REAL_TYPE
            ):
                continue
            values = entry.get("values")
            if not isinstance(values, dict):
                continue
            strength = ws._number(values.get("228"))
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
                    "execution_strength_trade_price": ws._number(values.get("10")),
                    "raw_item": str(entry.get("item") or ""),
                    "raw_real_type": EXECUTION_REAL_TYPE,
                }
            )
        return result

    def subscribe_one(self, connection, query_code: str) -> None:
        real_type = str(self.ws_config.get("type") or EXECUTION_REAL_TYPE).upper()
        connection.send(
            {
                "trnm": "REG",
                "grp_no": str(self.ws_config.get("group_no") or "41"),
                "refresh": "0",
                "data": [{"item": [query_code], "type": [real_type]}],
            }
        )

    def subscribe_many(self, connection, query_codes: list[str]) -> None:
        real_type = str(self.ws_config.get("type") or EXECUTION_REAL_TYPE).upper()
        connection.send(
            {
                "trnm": "REG",
                "grp_no": str(self.ws_config.get("group_no") or "41"),
                "refresh": "0",
                "data": [{"item": list(query_codes), "type": [real_type]}],
            }
        )

    def run(self) -> None:
        if not self.enabled:
            self._status(realtime_strength_ws_status="disabled")
            return
        url = str(self.ws_config.get("url") or "").strip()
        connect_timeout = max(3.0, float(self.ws_config.get("connect_timeout_sec") or 10))
        backoff = max(3.0, float(self.ws_config.get("reconnect_backoff_sec") or 10))
        while not self.stop_event.is_set():
            phase = ws._session_phase(self.config)
            if phase == "outside":
                self._status(realtime_strength_ws_status="outside_active_session")
                self.stop_event.wait(2.0)
                continue
            codes = list(self._current_codes()) if hasattr(self, "_current_codes") else []
            if not codes:
                self._status(realtime_strength_ws_status="waiting_top100")
                self.stop_event.wait(1.0)
                continue
            query_codes = [ws._query_code(self.config, code, phase) for code in codes]
            connection = None
            try:
                self._status(
                    realtime_strength_ws_status="connecting",
                    realtime_strength_ws_selected_code=codes[0],
                    realtime_strength_ws_selected_codes=list(codes),
                    realtime_strength_ws_selected_count=len(codes),
                    realtime_strength_ws_selected_source="canonical_top100",
                    realtime_strength_ws_query_codes=list(query_codes),
                    realtime_strength_ws_market_phase=phase,
                    realtime_strength_ws_real_type=EXECUTION_REAL_TYPE,
                    realtime_strength_ws_source_contract=PATCH_VERSION,
                )
                connection = ws._connect_websocket(url, connect_timeout)
                self._set_connection(connection)
                self._login(connection, connect_timeout)
                self._subscribe_top20(connection, query_codes)
                self._consume(
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

    ws.parse_realtime_strength_message = parse_realtime_strength_message
    updater_class._subscribe = subscribe_one
    updater_class._subscribe_top20 = subscribe_many
    updater_class.run = run
    updater_class._stockboard_execution_source_contract_installed = True
    updater_class._stockboard_execution_source_contract_version = PATCH_VERSION


def _install_strength_tr_contract() -> None:
    import realtime_v2.worker_rest_live_metrics_patch as rest

    if getattr(rest, "_stockboard_strength_tr_contract_installed", False):
        return
    original_parse = rest.parse_strength_payload

    def parse_strength_payload(*args, **kwargs):
        values = original_parse(*args, **kwargs)
        if not isinstance(values, dict):
            return values
        result = dict(values)
        if result.get("strength_5m") not in (None, ""):
            result["strength_source"] = STRENGTH_SOURCE
            result["strength_status"] = "ok"
        if result.get("execution_strength") not in (None, ""):
            result["execution_strength_source"] = STRENGTH_SOURCE
        return result

    rest.parse_strength_payload = parse_strength_payload
    rest._stockboard_strength_tr_contract_installed = True
    rest._stockboard_strength_tr_contract_version = PATCH_VERSION


def _install_program_contract(base) -> None:
    import kiwoom_data_provider as provider
    from realtime_v2.worker_market_metric_session_manager import (
        market_metric_phase,
        metric_target_trading_date,
    )

    if not getattr(provider, "_stockboard_program_api_contract_installed", False):
        original_post = provider._post_json

        def post_json(path, payload, headers=None, return_headers=False):
            next_headers = dict(headers or {})
            if str(next_headers.get("api-id") or "") == "ka90004":
                next_headers["api-id"] = PROGRAM_API_ID
            return original_post(
                path,
                payload,
                next_headers,
                return_headers=return_headers,
            )

        provider._post_json = post_json
        provider._stockboard_program_api_contract_installed = True
        provider._stockboard_program_api_contract_version = PATCH_VERSION

    updater_class = getattr(base, "ProgramNetUpdater", None)
    if updater_class is None:
        return

    def fetch_once(self) -> None:
        coordinator = get_shared_tr_coordinator()
        trade_date = metric_target_trading_date()
        phase = market_metric_phase()
        if not trade_date:
            self.state.set_program_net_error("program target trading date unresolved")
            return

        def physical_fetch():
            token = provider.issue_access_token()
            return provider.fetch_program_net(token, trade_date)

        try:
            result = coordinator.execute(
                provider="kiwoom_rest",
                tr_code=f"{PROGRAM_API_ID}_program_net",
                params={"scope": "stockboard_universe", "target_date": trade_date},
                trading_date=trade_date,
                market_session=phase,
                ttl_sec=max(15.0, float(self.interval_sec) * 0.8),
                wait_timeout_sec=max(30.0, float(self.interval_sec)),
                fetcher=physical_fetch,
            )
            values = result.get("values") if isinstance(result, dict) else None
            if isinstance(values, dict):
                status = "partial" if result.get("errors") or result.get("rate_limit") else "ok"
                self.state.apply_program_net_values(values, PROGRAM_SOURCE, status)
            with self.state.lock:
                self.state.status.update(
                    {
                        "tr_singleflight": coordinator.status(),
                        "program_request_target_trading_date": trade_date,
                        "program_request_market_phase": phase,
                        "program_api_id": PROGRAM_API_ID,
                        "program_source_contract": PATCH_VERSION,
                        "program_raw_samples": (result or {}).get("raw_samples") if isinstance(result, dict) else None,
                        "program_converted_samples": (result or {}).get("converted_samples") if isinstance(result, dict) else None,
                    }
                )
        except Exception as error:
            self.state.set_program_net_error(str(error))
            with self.state.lock:
                self.state.status.update(
                    {
                        "tr_singleflight": coordinator.status(),
                        "program_api_id": PROGRAM_API_ID,
                        "program_source_contract": PATCH_VERSION,
                        "program_source_status": "error_fail_closed",
                    }
                )

    updater_class._fetch_once = fetch_once
    updater_class._stockboard_program_source_contract_installed = True
    updater_class._stockboard_program_source_contract_version = PATCH_VERSION


def _install_display_contract(base) -> None:
    import realtime_v2.worker_five_metric_display_policy as display

    def contains(*tokens: str):
        lowered = tuple(token.lower() for token in tokens)

        def allowed(source: str) -> bool:
            text = str(source or "").lower()
            return bool(text) and any(token in text for token in lowered)

        return allowed

    execution = display.POLICIES.get("execution")
    if isinstance(execution, dict):
        execution["active_source"] = lambda row: str(
            row.get("execution_strength_source") or ""
        ) == EXECUTION_SOURCE
        execution["hold_source"] = contains(EXECUTION_SOURCE)
        execution["active_basis"] = "fresh_fid228_0A_websocket"

    strength5 = display.POLICIES.get("strength5")
    if isinstance(strength5, dict):
        strength5["active_source"] = lambda row: contains(STRENGTH_SOURCE)(
            str(row.get("strength_source") or "")
        )
        strength5["hold_source"] = contains(STRENGTH_SOURCE, "opt10045")
        strength5["max_age"] = lambda row: 420.0
        strength5["active_basis"] = "current_session_ka10045_snapshot"

    program = display.POLICIES.get("program")
    if isinstance(program, dict):
        program["active_source"] = lambda row: contains(PROGRAM_API_ID, "program_ws_0u")(
            str(row.get("program_net_source") or "")
        )
        program["hold_source"] = contains(PROGRAM_API_ID, "program_ws_0u")
        program["max_age"] = lambda row: 300.0
        program["active_basis"] = "current_session_ka90003"

    state_class = getattr(base, "State", None)
    if state_class is not None and not getattr(
        state_class, "_stockboard_aux_metric_source_contract_installed", False
    ):
        original_init = state_class.__init__

        def state_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)
            with self.lock:
                self.status.update(
                    {
                        "aux_metric_source_contract_installed": True,
                        "aux_metric_source_contract_version": PATCH_VERSION,
                        "execution_realtime_type": EXECUTION_REAL_TYPE,
                        "orderbook_realtime_type": ORDERBOOK_REAL_TYPE,
                        "strength_tr_api_id": STRENGTH_TREND_API_ID,
                        "program_tr_api_id": PROGRAM_API_ID,
                    }
                )

        state_class.__init__ = state_init
        state_class._stockboard_aux_metric_source_contract_installed = True
        state_class._stockboard_aux_metric_source_contract_version = PATCH_VERSION


def install(base) -> None:
    """Install source corrections after the existing auxiliary chain is assembled."""

    import realtime_v2.worker_rest_live_metrics_patch as rest
    import realtime_v2.worker_realtime_strength_ws_patch as ws

    if getattr(base, "_stockboard_aux_metric_source_contract_installed", False):
        return

    corrected_reader = _corrected_config(rest._read_config)
    rest._read_config = corrected_reader
    ws._read_config = corrected_reader

    _install_execution_contract(base)
    _install_strength_tr_contract()
    _install_program_contract(base)
    _install_display_contract(base)

    base._stockboard_aux_metric_source_contract_installed = True
    base._stockboard_aux_metric_source_contract_version = PATCH_VERSION
