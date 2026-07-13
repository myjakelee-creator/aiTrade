from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.theme_projection_flow_history_patch import install


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "realtime_v2" / "theme_projection_flow_history_patch.py"


class FakeBuilder:
    def __init__(self):
        pass

    def __call__(self, feature_version, rows, meta):
        return {
            "feature_version": feature_version,
            "rows": list(rows),
            "policy": {},
        }


def test_flow_history_derives_one_and_five_minute_values_from_shared_cumulative():
    module = SimpleNamespace(ThemeProjectionBuilder=FakeBuilder)
    install(module)
    builder = module.ThemeProjectionBuilder()

    first = builder(
        1,
        ({"stock_code": "005930", "trade_value_eok": 100.0},),
        {"snapshot_epoch": 1000.0, "trading_date": "20260713"},
    )
    second = builder(
        2,
        ({"stock_code": "005930", "trade_value_eok": 130.0},),
        {"snapshot_epoch": 1061.0, "trading_date": "20260713"},
    )
    third = builder(
        3,
        ({"stock_code": "005930", "trade_value_eok": 220.0},),
        {"snapshot_epoch": 1301.0, "trading_date": "20260713"},
    )

    assert "trade_value_1m_eok" not in first["rows"][0]
    assert second["rows"][0]["trade_value_1m_eok"] == 30.0
    assert third["rows"][0]["trade_value_5m_eok"] == 120.0
    assert third["policy"]["flow_history"] == "server_cumulative_delta_60s_300s"


def test_flow_history_resets_when_cumulative_value_moves_backwards():
    class ResetBuilder:
        def __init__(self):
            pass

        def __call__(self, _feature_version, rows, _meta):
            return {"rows": list(rows), "policy": {}}

    module = SimpleNamespace(ThemeProjectionBuilder=ResetBuilder)
    install(module)
    builder = module.ThemeProjectionBuilder()
    builder(1, ({"stock_code": "005930", "trade_value_eok": 100.0},), {"snapshot_epoch": 1000.0})
    result = builder(2, ({"stock_code": "005930", "trade_value_eok": 10.0},), {"snapshot_epoch": 1061.0})
    assert "trade_value_1m_eok" not in result["rows"][0]


def test_flow_history_patch_has_no_direct_market_or_browser_path():
    source = PATCH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "dynamicCall" not in source
    assert "kiwoom_data_provider" not in source
    assert "fetch(" not in source
    assert "document." not in source
