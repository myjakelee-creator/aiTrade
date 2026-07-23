from __future__ import annotations

import ast
from pathlib import Path

from scripts import stockboard_v2_collector_trace_policy_fix as policy

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stockboard_v2_collector_trace_policy_fix.py"


def _sample(**overrides):
    sample = {
        "provider_login_state": "connected",
        "provider_realreg_succeeded": True,
        "provider_qt_pump_running": True,
        "sender_connected": True,
        "provider_realdata_received_count": 100,
        "provider_trade_event_received_count": 90,
        "provider_trade_event_applied_count": 80,
        "sender_received_trade_count": 80,
        "sender_pending_trade_count": 0,
        "worker_trade_count": 70,
        "provider_trade_last_received_at": "2026-07-22T15:59:00+09:00",
        "provider_trade_last_received_code": "005930_AL",
        "provider_trade_last_fid10_raw": "+263500",
        "provider_trade_last_fid20_raw": "155900",
    }
    sample.update(overrides)
    return sample


def test_missing_optional_status_does_not_override_downstream_activity():
    first = _sample(
        provider_qt_pump_running=None,
        provider_trade_event_applied_count=None,
        provider_trade_last_received_at=None,
        provider_trade_last_received_code=None,
        provider_trade_last_fid10_raw=None,
        provider_trade_last_fid20_raw=None,
    )
    last = _sample(
        provider_qt_pump_running=None,
        provider_realdata_received_count=539,
        provider_trade_event_received_count=528,
        provider_trade_event_applied_count=None,
        sender_received_trade_count=518,
        worker_trade_count=412,
        provider_trade_last_received_at=None,
        provider_trade_last_received_code=None,
        provider_trade_last_fid10_raw=None,
        provider_trade_last_fid20_raw=None,
    )

    assert policy._optional_delta(
        first, last, "provider_trade_event_applied_count"
    ) is None
    assert policy._classify(first, last) == (
        "PRICE_PATH_ACTIVE_WITH_OPTIONAL_STATUS_UNKNOWN"
    )


def test_explicit_false_status_still_fails_closed():
    first = _sample()
    assert policy._classify(
        first, _sample(provider_qt_pump_running=False, worker_trade_count=71)
    ) == "QT_EVENT_PUMP_NOT_RUNNING"
    assert policy._classify(
        first, _sample(provider_realreg_succeeded=False, worker_trade_count=71)
    ) == "SETREALREG_NOT_READY"


def test_policy_wrapper_is_operator_only():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "PRICE_PATH_ACTIVE_WITH_OPTIONAL_STATUS_UNKNOWN" in source
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "dynamicCall",
        "CommRqData",
        "issue_access_token",
        "websocket",
    ):
        assert forbidden not in source
