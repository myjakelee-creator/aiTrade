from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, to_number

PATCH_VERSION = "aux_metric_runtime_policy_v1"


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _rank_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    code, row = item
    for key in ("candidate_rank", "model_rank", "pool_rank", "rank"):
        rank = _number(row.get(key))
        if rank is not None and rank > 0:
            return (0, int(rank), -(_number(row.get("trade_value_eok")) or 0.0), code)
    return (1, 999999, -(_number(row.get("trade_value_eok")) or 0.0), code)


def _resolve_top100_codes(state, limit: int = 100) -> list[str]:
    with state.lock:
        quotes = {
            normalize_code(code): dict(row)
            for code, row in state.quotes.items()
            if isinstance(row, dict) and normalize_code(code)
        }
    ranked = sorted(quotes.items(), key=_rank_key)
    resolved_limit = max(1, min(100, int(limit or 100)))
    return [code for code, _row in ranked[:resolved_limit]]


def install(base) -> None:
    """Align five-metric display retention and FID228 scope with actual runtime.

    Snapshot metrics are not realtime streams. Once a current-trading-date value from an
    approved source has been accepted, it remains visible until a newer successful value
    replaces it or the trading date rolls over. Age remains diagnostic metadata and does
    not erase the value. Realtime orderbook keeps its strict three-second contract and
    execution strength keeps its realtime transport freshness contract.

    The existing single FID228 WebSocket connection is expanded from Top20 to Top100.
    No QAx owner, price FID, REST thread, browser calculation, or additional connection is
    introduced.
    """

    import realtime_v2.worker_five_metric_display_policy as display_policy
    import realtime_v2.worker_realtime_strength_ws_patch as ws_module
    import realtime_v2.worker_realtime_strength_ws_top20_patch as scope_module

    state_class = getattr(base, "State", None)
    updater_class = ws_module.RealtimeStrengthWebSocket
    if state_class is None or getattr(
        state_class,
        "_stockboard_aux_metric_runtime_policy_installed",
        False,
    ):
        return

    # A current-session snapshot remains authoritative until a newer snapshot replaces it.
    # Source and trading-date validation in five_metric_display_policy stays mandatory.
    for metric, basis in (
        ("strength5", "current_session_ka10046_held_until_refresh"),
        ("program", "current_session_ka90004_held_until_refresh"),
        ("large_trade", "current_session_large_trade_held_until_refresh"),
    ):
        config = display_policy.POLICIES.get(metric)
        if isinstance(config, dict):
            config["max_age"] = lambda _row: 86_400.0
            config["active_basis"] = basis

    # The Top20 implementation already owns one connection and one-second batching.
    # Replace only its resolver so the same transport registers up to 100 symbols.
    scope_module.resolve_top_codes = _resolve_top100_codes

    original_state_init = state_class.__init__
    original_ws_initialize_status = updater_class._initialize_status

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["aux_metric_runtime_policy_installed"] = True
            self.status["aux_metric_runtime_policy_version"] = PATCH_VERSION
            self.status["snapshot_metric_retention_policy"] = (
                "current_trading_date_hold_until_successful_refresh"
            )
            self.status["realtime_strength_ws_scope"] = "top100"
            self.status["realtime_strength_ws_max_symbols"] = 100

    def ws_initialize_status(self) -> None:
        original_ws_initialize_status(self)
        with self.state.lock:
            self.state.status["realtime_strength_ws_scope"] = "top100"
            self.state.status["realtime_strength_ws_max_symbols"] = 100
            self.state.status["realtime_strength_ws_scope_policy_version"] = PATCH_VERSION

    state_class.__init__ = state_init
    updater_class._initialize_status = ws_initialize_status
    state_class._stockboard_aux_metric_runtime_policy_installed = True
