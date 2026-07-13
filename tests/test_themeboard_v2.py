from __future__ import annotations

import ast
import json
from pathlib import Path

from realtime_v2.theme_projection_engine import (
    ThemeMembershipLoader,
    ThemeProjectionBuilder,
)


ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "config" / "stockboard_theme_master.json"
ENGINE = ROOT / "realtime_v2" / "theme_projection_engine.py"
HUB_PATCH = ROOT / "realtime_v2" / "worker_board_data_hub_patch.py"
HTML = ROOT / "docs" / "themeboard.html"


def test_approved_theme_master_has_ten_weighted_themes():
    payload = json.loads(MASTER.read_text(encoding="utf-8"))
    assert len(payload["themes"]) == 10
    assert payload["weight_policy"] == "per_stock_sum_1"
    for code, memberships in payload["stock_memberships"].items():
        assert len(code) == 6 and code.isdigit()
        assert round(sum(float(item["weight"]) for item in memberships), 6) == 1.0


def test_weighted_projection_keeps_full_master_coverage_denominator(tmp_path: Path):
    master = tmp_path / "master.json"
    master.write_text(
        json.dumps(
            {
                "master_version": "test",
                "themes": {
                    "SEMI": {
                        "theme_name": "반도체",
                        "enabled": True,
                        "min_active_members": 1,
                    }
                },
                "stock_memberships": {
                    "005930": [
                        {
                            "theme_id": "SEMI",
                            "weight": 0.7,
                            "relation": "core",
                        }
                    ],
                    "000660": [
                        {
                            "theme_id": "SEMI",
                            "weight": 0.3,
                            "relation": "core",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    builder = ThemeProjectionBuilder(
        ThemeMembershipLoader(paths=[master], refresh_sec=1)
    )
    payload = builder(
        11,
        (
            {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "price": 100_000,
                "change_rate": 2.0,
                "trade_value_eok": 100.0,
                "trade_value_1m_eok": 10.0,
                "trade_value_5m_eok": 30.0,
                "candidate_score": 88.0,
                "large_trade_net_sum_eok": 1.2,
            },
        ),
        {},
    )

    row = payload["rows"][0]
    detail = payload["details"]["SEMI"]
    assert row["master_member_count"] == 2
    assert row["active_member_count"] == 1
    assert row["coverage_pct"] == 50.0
    assert row["coverage_status"] == "LOW_COVERAGE"
    assert row["trade_value_eok"] == 70.0
    assert row["trade_value_1m_eok"] == 7.0
    assert row["large_trade_net_eok"] == 0.84
    assert detail["members"][0]["leadership_role"] == "주도"
    assert "focus" not in row
    assert "focus" not in detail["members"][0]


def test_theme_projection_and_ui_follow_low_overhead_rules():
    engine_source = ENGINE.read_text(encoding="utf-8")
    hub_source = HUB_PATCH.read_text(encoding="utf-8")
    html_source = HTML.read_text(encoding="utf-8")

    ast.parse(engine_source)
    ast.parse(hub_source)
    assert "kiwoom_data_provider" not in engine_source
    assert "dynamicCall" not in engine_source
    assert "fetch_" not in engine_source
    assert "min_interval_ms: int = 1000" in engine_source
    assert '"candidate_rescore_allowed": False' in engine_source
    assert '"html_calculation_allowed": False' in engine_source

    assert '"/theme"' in hub_source
    assert '"/api/v2/hub/theme/stream"' in hub_source
    assert '"/api/v2/hub/theme/detail"' in hub_source
    assert "theme_payload(projection)" in hub_source
    assert "original_snapshot(self, limit)" in hub_source

    assert "/api/v2/hub/theme/stream?interval_ms=1000" in html_source
    assert "/api/v2/hub/theme/detail?theme_id=" in html_source
    assert ".sort(" not in html_source
    assert ".reduce(" not in html_source
    assert "FOCUS" not in html_source
    assert "demo" not in html_source.lower()
