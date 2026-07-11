from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.offhours_metric_timer_driver_patch import (
    _driver_status,
    _driver_tick_once,
    _pump_without_legacy_drain,
)


class DummyDrain:
    def __init__(
        self,
        *,
        fail: bool = False,
        mode: str = "idle",
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
        self.set_calls = []

    def setInterval(self, value):
        self.interval = int(value)
        self.set_calls.append(self.interval)

    def isActive(self):
        return self.active


class DummyProvider(SimpleNamespace):
    def __init__(self, drain):
        super().__init__(
            _running=True,
            _stockboard_offhours_strength_drain=drain,
        )


def test_timer_driver_runs_without_any_market_tick():
    drain = DummyDrain()
    provider = DummyProvider(drain)

    assert _driver_tick_once(provider, "qt_timer") is True
    assert _driver_tick_once(provider, "qt_timer") is True

    status = _driver_status(provider)
    assert drain.tick_calls == 2
    assert status["market_tick_independent"] is True
    assert status["legacy_pump_drain_suppressed"] is True
    assert status["timer_tick_count"] == 2
    assert status["drain_run_count"] == 2
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
    assert status["timer_interval_ms"] == 1000


def test_timer_driver_waits_safely_until_drain_exists():
    provider = DummyProvider(None)

    assert _driver_tick_once(provider, "qt_timer") is False

    status = _driver_status(provider)
    assert status["timer_tick_count"] == 1
    assert status["drain_run_count"] == 0
    assert status["timer_error_count"] == 0
    assert status["timer_last_source"] == "qt_timer"


def test_complete_mode_switches_from_fast_to_idle_timer():
    drain = DummyDrain(mode="rate_gap", next_mode="complete")
    provider = DummyProvider(drain)
    timer = DummyTimer(250)
    provider._offhours_metric_timer = timer
    provider._offhours_metric_timer_interval_ms = 250

    assert _driver_tick_once(provider, "qt_timer") is True

    status = _driver_status(provider)
    assert timer.interval == 10000
    assert timer.set_calls == [10000]
    assert status["timer_interval_ms"] == 10000
    assert status["timer_idle_interval_ms"] == 10000
    assert status["timer_interval_switch_count"] == 1
    assert status["timer_last_mode"] == "complete"


def test_active_mode_switches_back_to_fast_timer():
    drain = DummyDrain(mode="complete", next_mode="strength_inflight")
    provider = DummyProvider(drain)
    timer = DummyTimer(10000)
    provider._offhours_metric_timer = timer
    provider._offhours_metric_timer_interval_ms = 10000

    assert _driver_tick_once(provider, "qt_timer") is True

    status = _driver_status(provider)
    assert timer.interval == 250
    assert timer.set_calls == [250]
    assert status["timer_interval_ms"] == 250
    assert status["timer_last_mode"] == "strength_inflight"


def test_legacy_main_pump_cannot_call_drain_and_restores_it_afterward():
    drain = DummyDrain()
    provider = DummyProvider(drain)
    seen = []

    def original_pump(current_provider):
        seen.append(
            getattr(current_provider, "_stockboard_offhours_strength_drain", None)
        )
        return True

    assert _pump_without_legacy_drain(provider, original_pump) is True
    assert seen == [None]
    assert provider._stockboard_offhours_strength_drain is drain
