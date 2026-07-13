from __future__ import annotations

import json
from pathlib import Path

from realtime_v2.theme_projection_engine import (
    ThemeMembershipLoader,
    ThemeProjectionBuilder,
)


def test_theme_projection_uses_only_shared_feature_rows(tmp_path: Path):
    mapping = tmp_path / "theme_membership.json"
    mapping.write_text(
        json.dumps(
            {
                "themes": [
                    {
                        "theme_id": "semi",
                        "theme_name": "반도체",
                        "members": ["005930", "000660"],
                    },
                    {
                        "theme_id": "auto",
                        "theme_name": "자동차",
                        "members": ["005380"],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    builder = ThemeProjectionBuilder(
        ThemeMembershipLoader(paths=[mapping], refresh_sec=1)
    )
    payload = builder(
        7,
        (
            {
                "stock_code": "005930",
                "stock_name": "삼성전자",
                "change_rate": 2.0,
                "trade_value_eok": 1000,
                "candidate_score": 80,
            },
            {
                "stock_code": "000660",
                "stock_name": "SK하이닉스",
                "change_rate": -1.0,
                "trade_value_eok": 2000,
                "candidate_score": 90,
            },
            {
                "stock_code": "005380",
                "stock_name": "현대차",
                "change_rate": 3.0,
                "trade_value_eok": 500,
                "candidate_score": 70,
            },
        ),
        {},
    )

    assert payload["status"] == "READY"
    assert payload["input_feature_version"] == 7
    assert payload["policy"]["direct_tr_allowed"] is False
    assert payload["row_count"] == 2

    semi = next(row for row in payload["rows"] if row["theme_id"] == "semi")
    assert semi["active_member_count"] == 2
    assert semi["advancers"] == 1
    assert semi["decliners"] == 1
    assert semi["avg_change_rate"] == 0.5
    assert semi["trade_value_eok"] == 3000
    assert semi["top_stock_code"] == "000660"


def test_theme_projection_waits_without_membership_map(tmp_path: Path):
    missing = tmp_path / "missing.json"
    builder = ThemeProjectionBuilder(
        ThemeMembershipLoader(paths=[missing], refresh_sec=1)
    )
    payload = builder(3, tuple(), {})

    assert payload["status"] == "WAIT_THEME_MAP"
    assert payload["row_count"] == 0
    assert payload["policy"]["input"] == "board_data_hub_shared_feature_snapshot"


def test_theme_projection_module_contains_no_tr_fetch_path():
    source = Path("realtime_v2/theme_projection_engine.py").read_text(
        encoding="utf-8"
    )
    assert "kiwoom_data_provider" not in source
    assert "fetch_" not in source
    assert "dynamicCall" not in source
    assert "LatestOnlyProjectionWorker" in source
