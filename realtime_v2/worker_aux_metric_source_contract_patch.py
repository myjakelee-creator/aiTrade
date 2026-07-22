from __future__ import annotations

"""Restore the current Kiwoom auxiliary metric contracts without adding load.

The production price collector remains untouched. The existing single Kiwoom
WebSocket connection and existing single-flight REST owner are reused. This patch
only restores the current API identifiers and rejects values created while the
incorrect 0A/0C/ka10045/ka90003 experiment was active.
"""

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, now_text
from realtime_v2.tr_singleflight import get_shared_tr_coordinator

PATCH_VERSION = "aux_metric_source_contract_v3"
EXECUTION_REAL_TYPE = "0B"          # 주식체결
ORDERBOOK_REAL_TYPE = "0D"          # 주식호가잔량
STRENGTH_TREND_API_ID = "ka10046"   # 체결강도추이시간별
PROGRAM_API_ID = "ka90004"          # 종목별프로그램매매현황
EXECUTION_SOURCE = "kiwoom_rest_ws_0B_fid228"
ORDERBOOK_SOURCE = "kiwoom_rest_ws_0D_rotating"
LARGE_SOURCE = "kiwoom_rest_ws_0B_fid15"
STRENGTH_SOURCE = "ka10046_rest_lowload"
PROGRAM_SOURCE = "ka90004_tr_singleflight"


def _corrected_config(original_reader):
    def read_config() -> dict[str, Any]:
        raw = original_reader()
        config = deepcopy(raw) if isinstance(raw, dict) else {}

        execution = config.setdefault("realtime_strength_ws", {})
        execution["type"] = EXECUTION_REAL_TYPE
        execution["strength_field"] = "228"

        orderbook = config.setdefault("realtime_orderbook_ws", {})
        orderbook["type"] = ORDERBOOK_REAL_TYPE

        metrics = config.setdefault("metrics", {})
        strength = metrics.setdefault("strength", {})
        strength["api_id"] = STRENGTH_TREND_API_ID
        strength["purpose"] = "5분·20분·60분 장중 체결강도 추이 전용"
        return config

    return read_config


def _install_execution_contract() -> None:
    import realtime_v2.worker_approved_minute_pipeline as approved
    import realtime_v2.worker_realtime_strength_ws_patch as ws

    updater_class = ws.RealtimeStrengthWebSocket
    if getattr(updater_class, "_stockboard_execution_source_contract_installed", False):
        return

    ws.PATCH_VERSION = "kiwoom_ws_0B_fid228_v3"
    ws.WS_SOURCE = EXECUTION_SOURCE
    ws.WS_REAL_TYPE = EXECUTION_REAL_TYPE

    # The approved minute pipeline owns the final single WebSocket loop. Change
    # only its source constants; the connection, rotation and publish cadence stay.
    approved.TRADE_TYPE = EXECUTION_REAL_TYPE
    approved.ORDERBOOK_TYPE_DEFAULT = ORDERBOOK_REAL_TYPE
    approved.EXECUTION_SOURCE = EXECUTION_SOURCE
    approved.ORDERBOOK_SOURCE = ORDERBOOK_SOURCE
    approved.LARGE_SOURCE = LARGE_SOURCE

    def parse_realtime_strength_message(message: Any) -> list[dict[str, Any]]:
        payload = ws._decode_message(message)
        if not isinstance(payload, dict) or str(payload.get("trnm") or "").upper() != "REAL":
            return []
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        result: list[dict[str, Any]] = []
        for entry in data:
            if not isinstance(entry, dict) or str(entry.get("type") or "").upper() != EXECUTION_REAL_TYPE:
                continue
            values = entry.get("values")
            if not isinstance(values, dict):
                continue
            code = normalize_code(entry.get("item") or values.get("9001"))
            strength = ws._number(values.get("228"))
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

    ws.parse_realtime_strength_message = parse_realtime_strength_message
    updater_class._subscribe = subscribe_one
    updater_class._subscribe_top20 = subscribe_many
    updater_class._stockboard_execution_source_contract_installed = True
    updater_class._stockboard_execution_source_contract_version = PATCH_VERSION


def _install_strength_contract() -> None:
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
        # ka10046 must never become the realtime execution-strength source.
        result.pop("execution_strength", None)
        result.pop("execution_strength_source", None)
        result.pop("execution_strength_status", None)
        result.pop("execution_strength_updated_at", None)
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
            return provider.fetch_program_net(provider.issue_access_token(), trade_date)

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


def _remove_keys(target: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        target.pop(key, None)


def _purge_wrong_experiment_sources(state) -> None:
    orderbook_keys = (
        "bid_ask_ratio", "bid_pct", "ask_pct", "bid_volume", "ask_volume",
        "best_ask_price", "best_bid_price", "orderbook_received_at",
        "ui_bid_ask_ratio", "ui_bid_pct", "ui_ask_pct", "ui_bid_volume",
        "ui_ask_volume", "ui_best_ask_price", "ui_best_bid_price",
        "ui_orderbook_observed_at", "ui_orderbook_source_trading_date",
    )
    execution_keys = (
        "execution_strength", "last_valid_execution_strength",
        "execution_strength_received_at", "execution_strength_updated_at",
        "ui_execution_strength", "ui_execution_strength_observed_at",
        "ui_execution_source_trading_date",
    )
    strength_keys = (
        "strength_5m", "strength_20m", "strength_60m",
        "ui_strength_5m", "ui_strength_20m", "ui_strength_60m",
        "ui_strength_observed_at", "ui_strength_source_trading_date",
    )
    program_keys = (
        "program_net", "program_net_updated_at", "program_net_status",
        "program_source_trading_date", "_session_hold_program_date",
    )

    purged = {"orderbook": 0, "execution": 0, "strength5": 0, "program": 0}
    targets = [
        value
        for mapping in (state.daily_values_by_code, state.quotes)
        for value in mapping.values()
        if isinstance(value, dict)
    ]
    for target in targets:
        orderbook_source = str(target.get("orderbook_source") or "").lower()
        if "0c_rotating" in orderbook_source:
            _remove_keys(target, orderbook_keys)
            target.pop("orderbook_source", None)
            purged["orderbook"] += 1

        execution_source = str(target.get("execution_strength_source") or "").lower()
        if "0a_fid228" in execution_source:
            _remove_keys(target, execution_keys)
            target.pop("execution_strength_source", None)
            purged["execution"] += 1

        strength_source = str(target.get("strength_source") or "").lower()
        if "ka10045" in strength_source:
            _remove_keys(target, strength_keys)
            target.pop("strength_source", None)
            purged["strength5"] += 1

        program_source = str(target.get("program_net_source") or "").lower()
        if "ka90003" in program_source:
            _remove_keys(target, program_keys)
            target.pop("program_net_source", None)
            purged["program"] += 1

    state.status["aux_metric_wrong_experiment_purged"] = purged


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
        execution["active_source"] = lambda row: str(row.get("execution_strength_source") or "") == EXECUTION_SOURCE
        execution["hold_source"] = contains(EXECUTION_SOURCE)
        execution["active_basis"] = "fresh_fid228_0B_websocket"

    strength5 = display.POLICIES.get("strength5")
    if isinstance(strength5, dict):
        strength5["active_source"] = lambda row: contains(STRENGTH_SOURCE)(str(row.get("strength_source") or ""))
        strength5["hold_source"] = contains(STRENGTH_SOURCE, "opt10046")
        strength5["active_basis"] = "current_session_ka10046_snapshot"

    program = display.POLICIES.get("program")
    if isinstance(program, dict):
        program["active_source"] = lambda row: contains(PROGRAM_API_ID, "program_ws_0u")(str(row.get("program_net_source") or ""))
        program["hold_source"] = contains(PROGRAM_API_ID, "program_ws_0u")
        program["active_basis"] = "current_session_ka90004"

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(state_class, "_stockboard_aux_metric_source_contract_installed", False):
        return

    original_init = state_class.__init__

    def state_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        with self.lock:
            _purge_wrong_experiment_sources(self)
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
    import realtime_v2.worker_rest_live_metrics_patch as rest
    import realtime_v2.worker_realtime_strength_ws_patch as ws

    if getattr(base, "_stockboard_aux_metric_source_contract_installed", False):
        return

    corrected_reader = _corrected_config(rest._read_config)
    rest._read_config = corrected_reader
    ws._read_config = corrected_reader

    _install_execution_contract()
    _install_strength_contract()
    _install_program_contract(base)
    _install_display_contract(base)

    base._stockboard_aux_metric_source_contract_installed = True
    base._stockboard_aux_metric_source_contract_version = PATCH_VERSION
