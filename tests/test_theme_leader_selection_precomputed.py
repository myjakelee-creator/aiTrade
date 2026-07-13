from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2 import theme_projection_engine as engine
from realtime_v2.theme_leader_selection_patch import install


class DummyBuilder:
    def __init__(self) -> None:
        self._theme_summary_split_lock = threading.RLock()
        self._theme_latest_flow_rows_by_code = {
            "000001": {
                "stock_code": "000001",
                "stock_name": "상승주",
                "change_rate": 0.1,
                "amount_ratio": 0.2,
                "trade_value_1m_eok": 0.1,
                "trade_value_5m_eok": 0.5,
                "trade_value_eok": 10.0,
                "execution_strength": 80.0,
                "strength_5m": 80.0,
                "program_net": -1.0,
                "large_trade_net_sum_eok": -1.0,
            },
            "000002": {
                "stock_code": "000002",
                "stock_name": "하락주",
                "change_rate": -3.0,
                "amount_ratio": 10.0,
                "trade_value_1m_eok": 100.0,
                "trade_value_5m_eok": 500.0,
                "trade_value_eok": 1000.0,
                "execution_strength": 200.0,
                "strength_5m": 180.0,
                "program_net": 100.0,
                "large_trade_net_sum_eok": 50.0,
            },
        }
        self._theme_latest_mapping_by_id = {
            "T1": {
                "theme_id": "T1",
                "theme_name": "테스트",
                "members": [
                    {"stock_code": "000001"},
                    {"stock_code": "000002"},
                ],
            }
        }
        self._theme_latest_summary_by_id = {}

    def __call__(self, feature_version, rows, meta):
        return {
            "status": "READY",
            "input_feature_version": feature_version,
            "rows": [
                {
                    "theme_id": "T1",
                    "theme_name": "테스트",
                    "leaders": [],
                }
            ],
            "policy": {},
            "performance_breakdown": {},
            "trend_feature_status": {
                "hold_active": True,
                "trading_date": "20260713",
            },
        }

    def build_selected_detail(self, feature_version, theme_id, rows, meta):
        return {
            "status": "READY",
            "theme_id": theme_id,
            "theme": {
                "members": [
                    {"stock_code": "000001"},
                    {"stock_code": "000002"},
                ]
            },
            "policy": {},
        }


def test_precomputed_stock_metrics_keep_positive_stock_ahead_of_negative_money_leader():
    module = SimpleNamespace(
        ThemeProjectionBuilder=DummyBuilder,
        _first_number=engine._first_number,
        _fmt_pct=engine._fmt_pct,
        _tone=engine._tone,
        _fmt_eok=engine._fmt_eok,
        _fmt_number=engine._fmt_number,
    )
    install(module)
    builder = module.ThemeProjectionBuilder()

    payload = builder(1, tuple(), {})
    leaders = payload["rows"][0]["leaders"]

    assert leaders[0]["stock_code"] == "000001"
    assert leaders[0]["leadership_role"] == "주도"
    assert leaders[1]["leadership_role"] == "관찰"
    assert leaders[1]["leadership_score"] <= 39.0
    status = payload["leader_selection_status"]
    assert status["stock_metric_extract_passes"] == 1
    assert status["per_theme_raw_metric_reparse"] is False
    assert status["rank_method"] == "within_theme_two_pass_minmax"
    assert status["rank_metric_passes_per_theme"] == 2
    assert status["positive_stock_precedence"] is True
