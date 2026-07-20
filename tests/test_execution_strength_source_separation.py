from __future__ import annotations

import importlib
import threading

import realtime_v2.execution_strength_alias_patch as separation
import realtime_v2.worker_approved_minute_rollover_guard as rollover


def _base():
    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.received = None

        def _apply_close_metrics(self, event):
            self.received = event
            return event

    class Base:
        pass

    Base.State = State
    return Base


def test_install_disables_legacy_opt10046_alias_without_wrapping_close_metrics():
    base = _base()
    original_apply = base.State._apply_close_metrics

    separation.install(base)
    state = base.State()
    event = {
        "values": {
            "execution_strength": 87.34,
            "strength_source": "opt10046",
            "strength_5m": 87.34,
        }
    }
    state._apply_close_metrics(event)

    assert base.State._apply_close_metrics is original_apply
    assert state.received == event
    assert "last_valid_execution_strength" not in state.received["values"]
    assert state.status["execution_strength_alias_installed"] is False
    assert state.status["execution_strength_source_guard_installed"] is True


def test_opt10046_execution_alias_is_removed_but_strength5_is_preserved():
    values = separation.sanitize_execution_values(
        {
            "execution_strength": 87.34,
            "last_valid_execution_strength": 87.34,
            "execution_strength_source": "opt10046",
            "execution_strength_status": "cached",
            "execution_source_trading_date": "20260711",
            "strength_5m": 87.34,
            "strength_source": "ka10046_rest_lowload",
            "strength_source_trading_date": "20260716",
        }
    )

    assert "execution_strength" not in values
    assert "last_valid_execution_strength" not in values
    assert "execution_strength_source" not in values
    assert "execution_source_trading_date" not in values
    assert values["strength_5m"] == 87.34
    assert values["strength_source"] == "ka10046_rest_lowload"


def test_missing_execution_source_is_not_promoted_to_fid228():
    values = separation.sanitize_execution_values(
        {
            "execution_strength": 117.55,
            "last_valid_execution_strength": 117.55,
            "execution_source_trading_date": "20260711",
            "strength_5m": 117.55,
        }
    )

    assert "execution_strength" not in values
    assert "last_valid_execution_strength" not in values
    assert values["strength_5m"] == 117.55


def test_trusted_fid228_execution_value_survives_source_guard():
    values = separation.sanitize_execution_values(
        {
            "execution_strength": 112.7,
            "last_valid_execution_strength": 112.7,
            "execution_strength_source": "kiwoom_rest_ws_0B_fid228",
            "execution_strength_status": "published_60s_last_good",
            "execution_source_trading_date": "20260716",
            "strength_5m": 94.6,
        }
    )

    assert values["execution_strength"] == 112.7
    assert values["execution_strength_source"] == "kiwoom_rest_ws_0B_fid228"
    assert values["execution_source_trading_date"] == "20260716"
    assert values["strength_5m"] == 94.6


def test_rollover_fallback_does_not_relabel_opt10046_as_fid228():
    module = importlib.reload(rollover)
    separation._install_rollover_source_guard()

    polluted = module._approved_ui_fallback(
        {
            "execution_strength": 87.34,
            "last_valid_execution_strength": 87.34,
            "execution_strength_source": "opt10046",
            "execution_source_trading_date": "20260711",
            "strength_5m": 87.34,
            "strength_source": "ka10046_rest_lowload",
        }
    )
    trusted = module._approved_ui_fallback(
        {
            "execution_strength": 105.2,
            "execution_strength_source": "kiwoom_rest_ws_0B_fid228_close_hold",
            "execution_source_trading_date": "20260716",
        }
    )

    assert "execution_strength" not in polluted
    assert "execution_strength_source" not in polluted
    assert polluted["strength_5m"] == 87.34
    assert trusted["execution_strength"] == 105.2
    assert trusted["execution_strength_source"] == "kiwoom_rest_ws_0B_fid228_close_hold"
