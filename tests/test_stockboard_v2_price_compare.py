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
    assert doctor._selected_codes(rows, "005930,000660", 30) == ["005930", "000660"]
    assert doctor._delta(100, 90) == 10
    assert doctor._percent_delta(110, 100) == 10


def test_price_doctor_is_operator_only_and_reuses_existing_ka10032_provider():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "fetch_trade_value_top100" in source
    assert "issue_access_token" in source
    assert "operator_only_no_background_load" in source
    for forbidden in (
        "threading.Thread",
        "setInterval",
        "QAxWidget",
        "SetRealReg",
        "EventSource",
        "websocket",
    ):
        assert forbidden not in source


def test_root_cmd_routes_price_doctor_without_restart():
    source = CMD_PATH.read_text(encoding="utf-8-sig")
    assert 'if /I "%ACTION%"=="price-doctor"' in source
    assert 'py -3 "%PRICE_DOCTOR%"' in source
    price_block = source.split('if /I "%ACTION%"=="price-doctor"', 1)[1].split("goto direct_action", 1)[0]
    assert "restart" not in price_block.lower()
    assert "SAFE%\" -Action stop" not in price_block
