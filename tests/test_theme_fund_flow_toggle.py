from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import theme_projection_engine as engine
from realtime_v2.theme_projection_dual_rank_patch import install


def _module():
    class Builder:
        def __call__(self, feature_version, rows, meta):
            return {
                "status": "READY",
                "input_feature_version": feature_version,
                "rows": [dict(row) for row in rows],
                "policy": {},
                "performance_breakdown": {
                    "aggregate_ms": 1.0,
                    "score_sort_ms": 0.5,
                    "momentum_ms": 0.2,
                },
            }

    return SimpleNamespace(
        ThemeProjectionBuilder=Builder,
        _grade=engine._grade,
    )


def _theme(
    theme_id: str,
    *,
    ratio: float,
    one: float,
    five: float,
    program: float = 0.0,
    large: float = 0.0,
):
    return {
        "theme_id": theme_id,
        "theme_name": theme_id,
        "coverage_status": "READY",
        "coverage_pct": 100.0,
        "active_member_count": 3,
        "amount_ratio_member_count": 3,
        "avg_change_rate": 1.0,
        "breadth_pct": 66.7,
        "theme_amount_ratio": ratio,
        "trade_value_1m_eok": one,
        "trade_value_5m_eok": five,
        "change_momentum_1m": 0.1,
        "change_persistence_5m": 0.2,
        "program_net_eok": program,
        "large_trade_net_eok": large,
        "held_member_count": 0,
        "leaders": [],
    }


def test_fund_flow_bars_are_server_relative_and_zero_without_recent_money():
    module = _module()
    install(module)
    payload = module.ThemeProjectionBuilder()(
        1,
        (
            _theme("HOT", ratio=3.0, one=100.0, five=500.0, program=3.0, large=1.0),
            _theme("MID", ratio=1.0, one=10.0, five=50.0),
            _theme("ZERO", ratio=0.5, one=0.0, five=0.0),
        ),
        {},
    )

    by_id = {row["theme_id"]: row for row in payload["rows"]}
    assert by_id["HOT"]["fund_flow_1m_score"] > by_id["MID"]["fund_flow_1m_score"]
    assert by_id["HOT"]["fund_flow_5m_score"] > by_id["MID"]["fund_flow_5m_score"]
    assert by_id["ZERO"]["fund_flow_1m_score"] == 0.0
    assert by_id["ZERO"]["fund_flow_5m_score"] == 0.0

    for row in payload["rows"]:
        assert 0.0 <= row["fund_flow_1m_bar_pct"] <= 100.0
        assert 0.0 <= row["fund_flow_5m_bar_pct"] <= 100.0

    money_by_id = {row["theme_id"]: row for row in payload["money_rows"]}
    assert money_by_id["HOT"]["fund_flow_1m_text"] == by_id["HOT"]["fund_flow_1m_text"]
    assert payload["ranking_policy"]["fund_flow_bars"]["server_completed"] is True
    assert payload["ranking_policy"]["fund_flow_bars"]["browser_calculation_allowed"] is False
    assert payload["policy"]["fund_flow_bars"] == "server_relative_1m_5m"


def test_theme_card_second_click_closes_detail_and_uses_server_bar_fields():
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "realtime_v2" / "worker_theme_dual_rank_ui_patch.py"
    ).read_text(encoding="utf-8")

    assert "window.__themeDetailOpen = Boolean(selectedThemeId);" in source
    assert "nextThemeId===String(selectedThemeId||'')&&window.__themeDetailOpen" in source
    assert "window.__themeDetailOpen=false;" in source
    assert "if(!window.__themeDetailOpen){detail.hidden=true;return;}" in source
    assert "if(!window.__themeDetailOpen||!selectedThemeId||!lastPayload)return;" in source
    assert "theme.fund_flow_1m_bar_pct" in source
    assert "theme.fund_flow_5m_bar_pct" in source
    assert "theme.fund_flow_1m_text" in source
    assert "theme.fund_flow_5m_text" in source
    assert "1분쏠림" in source
    assert "5분쏠림" in source
    assert ".sort(" not in source
