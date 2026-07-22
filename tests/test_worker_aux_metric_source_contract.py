from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from realtime_v2 import worker_aux_metric_source_contract_patch as contract

ROOT = Path(__file__).resolve().parents[1]


def _run(script: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_runtime_config_restores_current_realtime_and_tr_contracts():
    reader = contract._corrected_config(
        lambda: {
            "realtime_strength_ws": {"type": "0A"},
            "realtime_orderbook_ws": {"type": "0C"},
            "metrics": {"strength": {"api_id": "ka10045"}},
        }
    )
    config = reader()
    assert config["realtime_strength_ws"]["type"] == "0B"
    assert config["realtime_orderbook_ws"]["type"] == "0D"
    assert config["metrics"]["strength"]["api_id"] == "ka10046"


def test_execution_parser_accepts_stock_trade_0b_and_rejects_priority_quote_0c():
    output = _run(
        r'''
from realtime_v2 import worker_aux_metric_source_contract_patch as contract
import realtime_v2.worker_realtime_strength_ws_patch as ws
contract._install_execution_contract()
def message(real_type):
    return {
        "trnm": "REAL",
        "data": [{
            "type": real_type,
            "item": "000660_AL",
            "values": {"20":"111501","10":"+1813000","228":"103.25","290":"2","9081":"3"},
        }],
    }
accepted = ws.parse_realtime_strength_message(message("0B"))
rejected = ws.parse_realtime_strength_message(message("0C"))
assert accepted[0]["stock_code"] == "000660"
assert accepted[0]["execution_strength"] == 103.25
assert accepted[0]["raw_real_type"] == "0B"
assert rejected == []
assert ws.WS_SOURCE == "kiwoom_rest_ws_0B_fid228"
print("execution_parser_contract_ok")
'''
    )
    assert "execution_parser_contract_ok" in output


def test_execution_subscription_uses_configured_0b_type():
    output = _run(
        r'''
from types import SimpleNamespace
from realtime_v2 import worker_aux_metric_source_contract_patch as contract
import realtime_v2.worker_realtime_strength_ws_patch as ws
contract._install_execution_contract()
sent=[]
connection=SimpleNamespace(send=lambda payload: sent.append(payload))
owner=SimpleNamespace(ws_config={"group_no":"41","type":"0B"})
ws.RealtimeStrengthWebSocket._subscribe_top20(owner, connection, ["000660_AL","005930_AL"])
assert sent[0]["data"][0]["type"] == ["0B"]
assert sent[0]["data"][0]["item"] == ["000660_AL","005930_AL"]
print("execution_subscription_contract_ok")
'''
    )
    assert "execution_subscription_contract_ok" in output


def test_strength_parser_uses_ka10046_and_does_not_overwrite_realtime_execution():
    output = _run(
        r'''
from realtime_v2 import worker_aux_metric_source_contract_patch as contract
import realtime_v2.worker_rest_live_metrics_patch as rest
contract._install_strength_contract()
values=rest.parse_strength_payload(
    {"cntr_str_tm":[{"cntr_tm":"111500","cntr_str":"103.25","cntr_str_5min":"101.40"}]},
    apply_five_minute=True,
    updated_at="2026-07-21T11:15:00+09:00",
)
assert values["strength_5m"] == 101.4
assert values["strength_source"] == "ka10046_rest_lowload"
assert "execution_strength" not in values
assert "execution_strength_source" not in values
print("strength_tr_contract_ok")
'''
    )
    assert "strength_tr_contract_ok" in output


def test_production_entrypoint_restores_integrated_ws_and_program_contracts():
    output = _run(
        r'''
import importlib
production = importlib.import_module("realtime_v2.worker64_guarded_large_bidask")
guarded = importlib.import_module("realtime_v2.worker64_guarded")
ws = importlib.import_module("realtime_v2.worker_realtime_strength_ws_patch")
approved = importlib.import_module("realtime_v2.worker_approved_minute_pipeline")
contract = importlib.import_module("realtime_v2.worker_aux_metric_source_contract_patch")
assert production is not None
assert getattr(guarded.base, "_stockboard_aux_metric_source_contract_installed", False) is True
assert getattr(guarded.base, "_stockboard_aux_metric_source_contract_version", None) == "aux_metric_source_contract_v3"
assert ws.WS_SOURCE == "kiwoom_rest_ws_0B_fid228"
assert approved.TRADE_TYPE == "0B"
assert approved.ORDERBOOK_TYPE_DEFAULT == "0D"
assert approved.EXECUTION_SOURCE == "kiwoom_rest_ws_0B_fid228"
assert approved.ORDERBOOK_SOURCE == "kiwoom_rest_ws_0D_rotating"
assert approved.LARGE_SOURCE == "kiwoom_rest_ws_0B_fid15"
assert ws.RealtimeStrengthWebSocket.run.__name__ == "integrated_run"
assert contract.EXECUTION_REAL_TYPE == "0B"
assert contract.ORDERBOOK_REAL_TYPE == "0D"
assert contract.STRENGTH_TREND_API_ID == "ka10046"
assert contract.PROGRAM_API_ID == "ka90004"
print("aux_metric_source_contract_ok")
'''
    )
    assert "aux_metric_source_contract_ok" in output
