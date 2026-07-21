from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import worker_aux_metric_source_contract_patch as contract

ROOT = Path(__file__).resolve().parents[1]


def test_corrected_runtime_config_uses_official_realtime_and_tr_contracts():
    reader = contract._corrected_config(
        lambda: {
            "realtime_strength_ws": {"type": "0B"},
            "realtime_orderbook_ws": {"type": "0D"},
            "metrics": {"strength": {"api_id": "ka10046"}},
        }
    )

    config = reader()

    assert config["realtime_strength_ws"]["type"] == "0A"
    assert config["realtime_orderbook_ws"]["type"] == "0C"
    assert config["metrics"]["strength"]["api_id"] == "ka10045"


def test_execution_parser_accepts_stock_trade_0a_and_rejects_priority_quote_0b(monkeypatch):
    import realtime_v2.worker_realtime_strength_ws_patch as ws

    monkeypatch.delattr(
        ws.RealtimeStrengthWebSocket,
        "_stockboard_execution_source_contract_installed",
        raising=False,
    )
    contract._install_execution_contract(SimpleNamespace())

    def message(real_type: str):
        return {
            "trnm": "REAL",
            "data": [
                {
                    "type": real_type,
                    "item": "000660_AL",
                    "values": {
                        "20": "111501",
                        "10": "+1813000",
                        "228": "103.25",
                        "290": "2",
                        "9081": "3",
                    },
                }
            ],
        }

    accepted = ws.parse_realtime_strength_message(message("0A"))
    rejected = ws.parse_realtime_strength_message(message("0B"))

    assert accepted[0]["stock_code"] == "000660"
    assert accepted[0]["execution_strength"] == 103.25
    assert accepted[0]["raw_real_type"] == "0A"
    assert rejected == []
    assert ws.WS_SOURCE == contract.EXECUTION_SOURCE


def test_execution_subscription_uses_configured_0a_type(monkeypatch):
    import realtime_v2.worker_realtime_strength_ws_patch as ws

    monkeypatch.delattr(
        ws.RealtimeStrengthWebSocket,
        "_stockboard_execution_source_contract_installed",
        raising=False,
    )
    contract._install_execution_contract(SimpleNamespace())

    sent = []
    connection = SimpleNamespace(send=lambda payload: sent.append(payload))
    owner = SimpleNamespace(ws_config={"group_no": "41", "type": "0A"})

    ws.RealtimeStrengthWebSocket._subscribe_top20(
        owner,
        connection,
        ["000660_AL", "005930_AL"],
    )

    assert sent[0]["data"][0]["type"] == ["0A"]
    assert sent[0]["data"][0]["item"] == ["000660_AL", "005930_AL"]


def test_strength_parser_relabels_intraday_tr_as_ka10045(monkeypatch):
    import realtime_v2.worker_rest_live_metrics_patch as rest

    monkeypatch.delattr(rest, "_stockboard_strength_tr_contract_installed", raising=False)
    contract._install_strength_tr_contract()

    values = rest.parse_strength_payload(
        {
            "cntr_str_tm": [
                {
                    "cntr_tm": "111500",
                    "cntr_str": "103.25",
                    "cntr_str_5min": "101.40",
                }
            ]
        },
        apply_five_minute=True,
        updated_at="2026-07-21T11:15:00+09:00",
    )

    assert values["strength_5m"] == 101.4
    assert values["strength_source"] == "ka10045_rest_lowload"


def test_production_entrypoint_installs_aux_metric_source_contract():
    script = r'''
import importlib
production = importlib.import_module("realtime_v2.worker64_guarded_large_bidask")
guarded = importlib.import_module("realtime_v2.worker64_guarded")
ws = importlib.import_module("realtime_v2.worker_realtime_strength_ws_patch")
assert production is not None
assert getattr(guarded.base, "_stockboard_aux_metric_source_contract_installed", False) is True
assert ws.WS_SOURCE == "kiwoom_rest_ws_0A_fid228"
assert getattr(ws.RealtimeStrengthWebSocket, "_stockboard_execution_source_contract_installed", False) is True
print("aux_metric_source_contract_ok")
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "aux_metric_source_contract_ok" in result.stdout
