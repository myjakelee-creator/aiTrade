from __future__ import annotations

import threading

import realtime_v2.worker_aux_metric_runtime_policy as runtime_policy
import realtime_v2.worker_five_metric_display_policy as display_policy
import realtime_v2.worker_realtime_strength_ws_patch as ws_module
import realtime_v2.worker_realtime_strength_ws_top20_patch as scope_module


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {
            f"{index:06d}": {
                "stock_code": f"{index:06d}",
                "rank": index,
                "trade_value_eok": 1000 - index,
            }
            for index in range(1, 121)
        }


class FakeBase:
    State = FakeState


def test_runtime_policy_expands_existing_single_connection_resolver_to_top100(monkeypatch):
    original_initialize = ws_module.RealtimeStrengthWebSocket._initialize_status
    try:
        runtime_policy.install(FakeBase)
        state = FakeState()
        codes = scope_module.resolve_top_codes(state, 100)

        assert len(codes) == 100
        assert codes[0] == "000001"
        assert codes[-1] == "000100"
        assert state.status["realtime_strength_ws_scope"] == "top100"
        assert state.status["realtime_strength_ws_max_symbols"] == 100
    finally:
        ws_module.RealtimeStrengthWebSocket._initialize_status = original_initialize


def test_snapshot_metrics_are_retained_until_successful_refresh():
    original = {
        metric: dict(display_policy.POLICIES[metric])
        for metric in ("strength5", "program", "large_trade")
    }
    original_initialize = ws_module.RealtimeStrengthWebSocket._initialize_status
    try:
        class LocalState(FakeState):
            pass

        class LocalBase:
            State = LocalState

        runtime_policy.install(LocalBase)

        for metric in ("strength5", "program", "large_trade"):
            assert display_policy.POLICIES[metric]["max_age"]({}) == 86_400.0
            assert "held_until_refresh" in display_policy.POLICIES[metric]["active_basis"]
    finally:
        ws_module.RealtimeStrengthWebSocket._initialize_status = original_initialize
        for metric, config in original.items():
            display_policy.POLICIES[metric].clear()
            display_policy.POLICIES[metric].update(config)
