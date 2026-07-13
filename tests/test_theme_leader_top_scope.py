from __future__ import annotations

import json
import types
from pathlib import Path

from realtime_v2.theme_leader_selection_patch import install as install_leaders
from realtime_v2.theme_projection_dual_rank_patch import install as install_dual_rank
from realtime_v2.theme_projection_summary_split_patch import install as install_summary


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "realtime_v2" / "theme_projection_engine.py"


def isolated_engine():
    module = types.ModuleType("theme_projection_engine_top_scope_test")
    module.__file__ = str(ENGINE_PATH)
    source = ENGINE_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(ENGINE_PATH), "exec"), module.__dict__)
    install_summary(module)
    install_dual_rank(module)
    install_leaders(module)
    return module


def mapping_file(tmp_path: Path) -> Path:
    themes = []
    for index in range(1, 26):
        themes.append(
            {
                "theme_id": f"T{index:02d}",
                "theme_name": f"테마{index:02d}",
                "members": [f"{index:06d}"],
            }
        )
    path = tmp_path / "themes.json"
    path.write_text(
        json.dumps(
            {
                "master_version": "TOP_SCOPE_TEST",
                "themes": themes,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def rows() -> tuple[dict, ...]:
    result = []
    for index in range(1, 26):
        money = float(26 - index)
        result.append(
            {
                "stock_code": f"{index:06d}",
                "stock_name": f"종목{index:02d}",
                "price": 1000.0,
                "change_rate": float(index),
                "trade_value_eok": money * 100.0,
                "trade_value_1m_eok": money,
                "trade_value_5m_eok": money * 5.0,
                "amount_ratio": money,
                "candidate_score": 50.0,
                "candidate_grade": "C",
                "execution_strength": 100.0 + index,
                "strength_5m": 100.0 + index,
                "program_net": float(index),
                "large_trade_net_sum_eok": float(index) / 10.0,
                "large_trade_net_count": 1,
            }
        )
    return tuple(result)


def test_only_top_view_union_gets_precise_summary_leaders_and_detail_is_always_precise(
    tmp_path,
):
    module = isolated_engine()
    loader = module.ThemeMembershipLoader(paths=[mapping_file(tmp_path)], refresh_sec=1)
    builder = module.ThemeProjectionBuilder(loader=loader)
    feature_rows = rows()

    payload = builder(1, feature_rows, {"snapshot_epoch": 1000.0})
    status = payload["leader_selection_status"]

    assert status["top_per_view"] == 10
    assert status["precise_theme_count"] == 20
    assert status["fallback_theme_count"] == 5
    assert status["summary_member_scope"] == "top_momentum_money_union_only"
    assert status["selected_detail_always_precise"] is True

    precise = [
        row for row in payload["rows"] if row.get("leader_precision") == "precise_top_union"
    ]
    fallback = [
        row for row in payload["rows"] if row.get("leader_precision") == "summary_fallback"
    ]
    assert len(precise) == 20
    assert len(fallback) == 5

    fallback_id = str(fallback[0]["theme_id"])
    detail = builder.build_selected_detail(
        1,
        fallback_id,
        feature_rows,
        {"snapshot_epoch": 1000.0},
    )
    assert detail["status"] == "READY"
    assert detail["theme"]["leader_precision"] == "selected_detail_precise"
    assert detail["leader_detail_status"]["precise"] is True
    assert detail["policy"]["selected_detail_always_precise"] is True
