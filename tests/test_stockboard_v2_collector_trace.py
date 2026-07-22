from __future__ import annotations

import ast
from pathlib import Path

from scripts import stockboard_v2_collector_trace as trace

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stockboard_v2_collector_trace.py"
CMD_PATH = ROOT / "stockboard_v2_large.cmd"


def _sample(**overrides):
    base = {
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
    }
    base.update(overrides)
    return base


def test_extract_sample_reads_existing_nested_collector_status():
    payload = {
        "ts": "2026-07-22T15:00:00.100+09:00",
        "market_session": {"phase_label": "정규장"},
        "status": {
            "event_count": 500,
            "trade_count": 450,
            "collector_status": {
                "ts": "2026-07-22T15:00:00+09:00",
                "status": {
                    "login_state": "connected",
                    "realreg_succeeded": True,
                    "realreg_code_count": 100,
                    "realdata_received_count": 1000,
                    "trade_event_received_count": 900,
                    "trade_event_applied_count": 850,
                    "qt_pump_running": True,
                    "trade_last_received_code": "005930_AL",
                    "trade_last_fid10_raw": "+263500",
                    "trade_last_fid20_raw": "150000",
                },
                "sender_stats": {
                    "connected": True,
                    "received_trade_count": 850,
                    "sent_count": 700,
                    "pending_trade_count": 2,
                },
            },
        },
    }

    sample = trace._extract_sample(payload, "2026-07-22T15:00:00.200+09:00")

    assert sample["phase"] == "정규장"
    assert sample["provider_realdata_received_count"] == 1000
    assert sample["provider_trade_event_received_count"] == 900
    assert sample["provider_trade_event_applied_count"] == 850
    assert sample["sender_received_trade_count"] == 850
    assert sample["worker_trade_count"] == 450
    assert sample["provider_trade_last_received_code"] == "005930_AL"
    assert sample["provider_trade_last_fid10_raw"] == "+263500"


def test_classification_finds_first_inactive_stage():
    first = _sample()

    assert trace._classify(first, _sample(provider_login_state="failed")) == (
        "QAX_LOGIN_NOT_CONNECTED"
    )
    assert trace._classify(first, _sample(provider_realreg_succeeded=False)) == (
        "SETREALREG_NOT_READY"
    )
    assert trace._classify(first, _sample(provider_qt_pump_running=False)) == (
        "QT_EVENT_PUMP_NOT_RUNNING"
    )
    assert trace._classify(first, _sample(sender_connected=False)) == (
        "EVENT_SENDER_NOT_CONNECTED"
    )
    assert trace._classify(first, _sample()) == "NO_QAX_REALDATA_CALLBACK"
    assert trace._classify(
        first,
        _sample(provider_realdata_received_count=101),
    ) == "QAX_REALDATA_WITHOUT_STOCK_TRADE"
    assert trace._classify(
        first,
        _sample(
            provider_realdata_received_count=101,
            provider_trade_event_received_count=91,
        ),
    ) == "PROVIDER_TRADE_NOT_APPLIED"
    assert trace._classify(
        first,
        _sample(
            provider_realdata_received_count=101,
            provider_trade_event_received_count=91,
            provider_trade_event_applied_count=81,
        ),
    ) == "STORE_TO_EVENT_SENDER_GAP"
    assert trace._classify(
        first,
        _sample(
            provider_realdata_received_count=101,
            provider_trade_event_received_count=91,
            provider_trade_event_applied_count=81,
            sender_received_trade_count=81,
            sender_pending_trade_count=1,
        ),
    ) == "EVENT_SENDER_PENDING_TRADE"
    assert trace._classify(
        first,
        _sample(
            provider_realdata_received_count=101,
            provider_trade_event_received_count=91,
            provider_trade_event_applied_count=81,
            sender_received_trade_count=81,
            worker_trade_count=71,
        ),
    ) == "PRICE_PATH_ACTIVE"


def test_collector_trace_is_operator_only_and_does_not_call_kiwoom():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)

    assert "operator_only_no_production_path_change" in source
    assert "api/v2/stream" in source
    assert "api/v2/snapshot" in source
    for forbidden in (
        "issue_access_token",
        "fetch_trade_value_top100",
        "QAxWidget",
        "SetRealReg",
        "dynamicCall",
        "CommRqData",
        "requests.",
        "websocket",
    ):
        assert forbidden not in source


def test_root_cmd_routes_collector_trace_without_restart():
    source = CMD_PATH.read_text(encoding="utf-8-sig")
    assert 'if /I "%ACTION%"=="collector-trace"' in source
    assert 'py -3 "%COLLECTOR_TRACE%" --duration-sec 15' in source
    block = source.split('if /I "%ACTION%"=="collector-trace"', 1)[1].split(
        "goto direct_action", 1
    )[0]
    assert "restart" not in block.lower()
    assert '"%SAFE%"' not in block
    assert "PREFLIGHT" not in block
