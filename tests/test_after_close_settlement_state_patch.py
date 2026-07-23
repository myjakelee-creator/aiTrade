from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

from realtime_v2 import after_close_settlement_state_patch as patch


class _State:
    def __init__(self, received_at=None):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "received_at": received_at,
            }
        }


def _session(phase="closed"):
    return SimpleNamespace(
        phase=phase,
        trading_date="20260723",
        windows={"aftermarket_end": "20:00"},
    )


def test_late_event_after_20_is_grace():
    state = _State("2026-07-23T20:02:00+09:00")
    info = patch.settlement_state(
        state,
        _session(),
        datetime.fromisoformat("2026-07-23T20:03:00+09:00"),
    )
    assert info["state"] == "late_arrival_grace"
    assert info["quiet_sec"] == 60.0


def test_quiet_before_hard_cutoff_is_provisional():
    state = _State("2026-07-23T19:59:00+09:00")
    info = patch.settlement_state(
        state,
        _session(),
        datetime.fromisoformat("2026-07-23T20:10:00+09:00"),
    )
    assert info["state"] == "provisional_close"
    assert info["quiet_sec"] == 660.0


def test_no_memory_after_restart_waits_for_recovery_before_hard_cutoff():
    state = _State(None)
    info = patch.settlement_state(
        state,
        _session(),
        datetime.fromisoformat("2026-07-23T20:05:00+09:00"),
    )
    assert info["state"] == "restart_recovery_wait"


def test_hard_cutoff_is_final_hold_without_discarding_values():
    state = _State("2026-07-23T20:12:00+09:00")
    info = patch.settlement_state(
        state,
        _session(),
        datetime.fromisoformat("2026-07-23T20:31:00+09:00"),
    )
    assert info["state"] == "final_close_hold"
    assert state.quotes["005930"]["received_at"] == "2026-07-23T20:12:00+09:00"


def test_weekend_and_before_market_are_final_hold():
    state = _State("2026-07-24T20:00:01+09:00")
    for phase in ("weekend", "holiday", "before_market"):
        assert patch.settlement_state(
            state,
            _session(phase),
            datetime.fromisoformat("2026-07-25T10:00:00+09:00"),
        )["state"] == "final_close_hold"


def test_active_session_is_not_changed():
    state = _State("2026-07-23T19:30:00+09:00")
    info = patch.settlement_state(
        state,
        _session("aftermarket"),
        datetime.fromisoformat("2026-07-23T19:31:00+09:00"),
    )
    assert info["state"] == "active_session"
