from __future__ import annotations

"""Bind the strict momentum evaluator to the actual production stage closure."""

from typing import Any, Callable

from realtime_v2.common import normalize_code

PATCH_VERSION = "momentum_accuracy_stage_bridge_v1"


def _closure_value(function: Callable[..., Any], name: str) -> Any:
    freevars = tuple(getattr(function.__code__, "co_freevars", ()))
    closure = tuple(getattr(function, "__closure__", ()) or ())
    mapping = {
        key: cell.cell_contents
        for key, cell in zip(freevars, closure)
    }
    if name not in mapping:
        raise RuntimeError(
            f"momentum closure {getattr(function, '__name__', '<unknown>')} "
            f"does not expose {name}; available={sorted(mapping)}"
        )
    return mapping[name]


def install(base) -> None:
    """Install strict momentum processing on the real approved-event stage."""

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_momentum_accuracy_stage_bridge_installed", False
    ):
        return

    policy_stage = getattr(state_class, "stage_approved_trade_events", None)
    if not callable(policy_stage):
        raise AttributeError("production momentum policy stage is unavailable")

    momentum_stage = _closure_value(policy_stage, "original_stage")
    approved_stage = _closure_value(momentum_stage, "original_stage")
    old_process_events = _closure_value(momentum_stage, "process_events")
    old_finalize_candle = _closure_value(old_process_events, "finalize_candle")
    observe_source = _closure_value(policy_stage, "observe_source")
    refresh_alert_cache = _closure_value(policy_stage, "refresh_alert_cache")

    from realtime_v2 import worker_momentum_1m_patch as momentum

    # The original implementation keeps these functions inside install() closures.
    # Expose them before the strict accuracy patch captures and wraps them.
    momentum.process_events = old_process_events
    momentum.finalize_candle = old_finalize_candle

    from realtime_v2.worker_momentum_accuracy_patch import install as install_accuracy

    install_accuracy(base)
    strict_process_events = getattr(momentum, "process_events", None)
    if not callable(strict_process_events) or strict_process_events is old_process_events:
        raise RuntimeError("strict momentum process_events was not installed")

    def stage_events(self, events):
        event_list = events if isinstance(events, list) else []
        result = approved_stage(self, events)
        changed = strict_process_events(self, event_list)
        trading_date = momentum._expected_date()
        codes = {
            normalize_code(event.get("stock_code"))
            for event in event_list
            if isinstance(event, dict)
        }
        with self.lock:
            for code in codes:
                if code:
                    changed = observe_source(self, code, trading_date) or changed
            if changed:
                refresh_alert_cache(
                    self,
                    momentum._system_minute_key(),
                    force=True,
                )
                self.status["momentum_accuracy_stage_last_event_count"] = len(event_list)
                self.status["momentum_accuracy_stage_last_trading_date"] = trading_date
        if changed:
            rebuild = getattr(self, "request_background_rebuild", None)
            if callable(rebuild):
                rebuild(reason="momentum_accuracy_signal_change", force=False)
        return result

    state_class.stage_approved_trade_events = stage_events
    state_class._stockboard_momentum_accuracy_stage_bridge_installed = True
    state_class._stockboard_momentum_accuracy_stage_owner = PATCH_VERSION
