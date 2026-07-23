from __future__ import annotations

import ast
from pathlib import Path

from scripts import stockboard_v2_price_compare as doctor

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "stockboard_v2_price_compare.py"
CMD_PATH = ROOT / "stockboard_v2_large.cmd"


def test_price_compare_helpers_are_deterministic():
    rows = [
        {"stock_code": "000660"},
        {"stock_code": "005930_AL"},
        {"stock_code": "000660"},
    ]
    assert doctor._selected_codes(rows, "", 30) == ["000660", "005930"]
    assert doctor._selected_codes(rows, "005930,000660", 30) == [
        "005930",
        "000660",
    ]
    assert doctor._delta(100, 90) == 10
    assert doctor._percent_delta(110, 100) == 10


def test_kiwoom_rank_is_recomputed_after_stockboard_eligibility_filter():
    rest_rows = [
        {"stock_code": "069500", "original_rank": 1},  # excluded ETF
        {"stock_code": "000660", "original_rank": 2},
        {"stock_code": "500001", "original_rank": 3},  # excluded ETN
        {"stock_code": "005930", "original_rank": 4},
    ]
    eligible, market = doctor._eligible_rest_ranks(
        rest_rows, {"000660", "005930"}
    )
    assert market == {
        "069500": 1.0,
        "000660": 2.0,
        "500001": 3.0,
        "005930": 4.0,
    }
    assert eligible == {"000660": 1, "005930": 2}


def test_rank_trade_value_gate_requires_exact_eligible_rank_and_tight_value_match():
    passing = [
        {
            "local_rank": rank,
            "rest_rank": rank,
            "rank_delta": 0.0,
            "trade_value_delta_pct": 0.1,
            "rest_found": True,
        }
        for rank in range(1, 21)
    ]
    gate, summary = doctor._gate_result(
        passing,
        rank_gate_limit=20,
        trade_value_tolerance_pct=0.5,
    )
    assert gate == "PASS"
    assert summary["rank_basis"] == doctor.RANK_BASIS
    assert summary["rank_gate_exact_count"] == 20
    assert summary["trade_value_gate_within_tolerance_count"] == 20

    rank_mismatch = [dict(row) for row in passing]
    rank_mismatch[4]["rest_rank"] = 6
    rank_mismatch[4]["rank_delta"] = -1.0
    gate, _summary = doctor._gate_result(
        rank_mismatch,
        rank_gate_limit=20,
        trade_value_tolerance_pct=0.5,
    )
    assert gate == "REVIEW"

    value_mismatch = [dict(row) for row in passing]
    value_mismatch[9]["trade_value_delta_pct"] = 0.51
    gate, _summary = doctor._gate_result(
        value_mismatch,
        rank_gate_limit=20,
        trade_value_tolerance_pct=0.5,
    )
    assert gate == "REVIEW"


def test_price_doctor_is_operator_only_and_reuses_existing_ka10032_provider():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "fetch_trade_value_top100" in source
    assert "issue_access_token" in source
    assert "operator_only_no_background_load" in source
    assert "rank_trade_value_gate" in source
    assert "local_trade_value_eok" in source
    assert "rest_trade_value_eok" in source
    assert "rest_eligible_rank" in source
    assert "rest_market_rank" in source
    assert doctor.RANK_BASIS in source
    for forbidden in (
        "threading.Thread",
        "setInterval",
        "QAxWidget",
        "SetRealReg",
        "EventSource",
        "websocket",
    ):
        assert forbidden not in source


def test_root_cmd_routes_price_doctor_and_trace_without_restart():
    source = CMD_PATH.read_text(encoding="utf-8-sig")
    assert 'if /I "%ACTION%"=="price-doctor"' in source
    assert 'py -3 "%PRICE_DOCTOR%"' in source
    price_block = source.split(
        'if /I "%ACTION%"=="price-doctor"', 1
    )[1].split('if /I "%ACTION%"=="price-trace"', 1)[0]
    assert "restart" not in price_block.lower()
    assert 'SAFE%" -Action stop' not in price_block

    assert 'if /I "%ACTION%"=="price-trace"' in source
    assert 'py -3 "%PRICE_TRACE%" --duration-sec 15' in source
    trace_block = source.split(
        'if /I "%ACTION%"=="price-trace"', 1
    )[1].split("goto direct_action", 1)[0]
    assert "restart" not in trace_block.lower()
    assert 'SAFE%" -Action stop' not in trace_block
