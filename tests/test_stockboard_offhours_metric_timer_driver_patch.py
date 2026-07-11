from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.offhours_metric_timer_driver_patch import (
    _driver_status,
    _driver_tick_once,
)


class DummyDrain:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.tick_calls = 0
        self.mode = "idle"
        self.last_error = None
        self.next_request_at = 10.0
        self.last_refresh_at = 10.0

    def tick(self):
        self.tick_calls += 1
        if self.fail:
            raise RuntimeError("synthetic drain failure")


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
    assert status["timer_tick_count"] == 2
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


def test_timer_driver_waits_safely_until_drain_exists():
    provider = DummyProvider(None)

    assert _driver_tick_once(provider, "qt_timer") is False

    status = _driver_status(provider)
    assert status["timer_tick_count"] == 1
    assert status["timer_error_count"] == 0
    assert status["timer_last_source"] == "qt_timer"
