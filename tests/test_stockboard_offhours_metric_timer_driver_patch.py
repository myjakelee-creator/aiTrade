from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.offhours_metric_timer_driver_patch import (
    _driver_status,
    _driver_tick_once,
    _unwrap_legacy_drain_pump,
)


class DummyDrain:
    def __init__(
        self,
        *,
        fail: bool = False,
        mode: str = "rate_gap",
        next_mode: str | None = None,
    ):
        self.fail = fail
        self.tick_calls = 0
        self.mode = mode
        self.next_mode = next_mode
        self.last_error = None
        self.next_request_at = 10.0
        self.last_refresh_at = 10.0

    def tick(self):
        self.tick_calls += 1
        if self.fail:
            raise RuntimeError("synthetic drain failure")
        if self.next_mode is not None:
            self.mode = self.next_mode


class DummyTimer:
    def __init__(self, interval: int = 250):
        self.interval = interval
        self.active = True

    def isActive(self):
        return self.active


class DummyProvider(SimpleNamespace):
    def __init__(self, drain):
        super().__init__(
            _running=True,
            _stockboard_offhours_strength_drain=drain,
        )


def test_timer_heartbeat_runs_without_market_tick_and_gates_heavy_drain():
    drain = DummyDrain(mode="complete")
    provider = DummyProvider(drain)

    assert _driver_tick_once(provider, "qt_timer") is True
    assert _driver_tick_once(provider, "qt_timer") is True

    status = _driver_status(provider)
    assert drain.tick_calls == 1
    assert status["market_tick_independent"] is True
    assert status["timer_tick_count"] == 2
    assert status["drain_run_count"] == 1
    assert status["drain_skip_count"] == 1
    assert status["timer_interval_ms"] == 250
    assert status["effective_drain_interval_ms"] == 10000
    assert status["next_drain_in_sec"] > 0
    assert status["timer_last_source"] == "qt_timer"
    assert status["timer_last_tick_age_sec"] is not None
    assert status["timer_error_count"] == 0


def test_timer_driver_recovers_from_one_bad_completion_tick():
    drain = DummyDrain(fail=True)
    provider = DummyProvider(drain)

    assert _driver_tick_once(provider, "qt_timer") is False

    status = _driver_status(provider)
    assert drain.tick_calls == 1
    assert status["timer_error_count"] == 1
    assert "synthetic drain failure" in status["timer_last_error"]
    assert drain.mode == "timer_driver_error_recovered"
    assert drain.next_request_at == 0.0
    assert drain.last_refresh_at == 0.0
    assert status["timer_interval_ms"] == 250
    assert status["effective_drain_interval_ms"] == 1000
    assert status["next_drain_in_sec"] > 0


def test_timer_driver_waits_safely_until_drain_exists():
    provider = DummyProvider(None)

    assert _driver_tick_once(provider, "qt_timer") is False

    status = _driver_status(provider)
    assert status["timer_tick_count"] == 1
    assert status["drain_run_count"] == 0
    assert status["timer_error_count"] == 0
    assert status["timer_last_source"] == "qt_timer"


def test_complete_mode_keeps_physical_timer_fast_but_gates_drain_to_idle_cadence():
    drain = DummyDrain(mode="rate_gap", next_mode="complete")
    provider = DummyProvider(drain)
    provider._offhours_metric_timer = DummyTimer(250)

    assert _driver_tick_once(provider, "qt_timer") is True

    status = _driver_status(provider)
    assert provider._offhours_metric_timer.interval == 250
    assert status["timer_interval_ms"] == 250
    assert status["timer_idle_interval_ms"] == 10000
    assert status["effective_drain_interval_ms"] == 10000
    assert status["timer_interval_switch_count"] == 1
    assert status["timer_last_mode"] == "complete"


def test_active_mode_returns_effective_drain_cadence_to_250ms():
    drain = DummyDrain(mode="complete", next_mode="strength_inflight")
    provider = DummyProvider(drain)
    provider._offhours_metric_timer = DummyTimer(250)
    provider._offhours_metric_timer_effective_drain_interval_ms = 10000

    assert _driver_tick_once(provider, "qt_timer", force=True) is True

    status = _driver_status(provider)
    assert provider._offhours_metric_timer.interval == 250
    assert status["timer_interval_ms"] == 250
    assert status["effective_drain_interval_ms"] == 250
    assert status["timer_last_mode"] == "strength_inflight"


def test_pump_fallback_force_bypasses_future_drain_deadline():
    drain = DummyDrain(mode="complete")
    provider = DummyProvider(drain)

    assert _driver_tick_once(provider, "qt_timer") is True
    assert _driver_tick_once(provider, "qt_timer") is True
    assert drain.tick_calls == 1

    assert _driver_tick_once(provider, "pump_fallback", force=True) is True
    assert drain.tick_calls == 2


def test_legacy_drain_wrapper_is_unwrapped_without_hiding_qt_timer_state():
    drain = DummyDrain()
    provider = DummyProvider(drain)
    base_calls = []

    def base_qt_pump(current_provider):
        # The drain must remain visible while QApplication.processEvents would run.
        base_calls.append(
            getattr(current_provider, "_stockboard_offhours_strength_drain", None)
        )
        return True

    def make_legacy_wrapper(original_pump):
        def pump(current_provider):
            ok = original_pump(current_provider)
            current_drain = getattr(
                current_provider,
                "_stockboard_offhours_strength_drain",
                None,
            )
            if current_drain is not None:
                current_drain.tick()
            return ok

        return pump

    legacy_pump = make_legacy_wrapper(base_qt_pump)
    unwrapped, changed = _unwrap_legacy_drain_pump(legacy_pump)

    assert changed is True
    assert unwrapped is base_qt_pump
    assert unwrapped(provider) is True
    assert base_calls == [drain]
    assert drain.tick_calls == 0
