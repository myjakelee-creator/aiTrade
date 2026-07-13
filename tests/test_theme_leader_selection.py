from __future__ import annotations

import json
import types
from pathlib import Path

from realtime_v2.theme_leader_detail_display_patch import (
    install as install_detail_display,
)
from realtime_v2.theme_leader_selection_patch import install as install_leaders
from realtime_v2.theme_projection_dual_rank_patch import install as install_dual_rank
from realtime_v2.theme_projection_summary_split_patch import install as install_summary


ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "realtime_v2" / "theme_projection_engine.py"


def isolated_engine():
    module = types.ModuleType("theme_projection_engine_leader_test")
    module.__file__ = str(ENGINE_PATH)
    source = ENGINE_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(ENGINE_PATH), "exec"), module.__dict__)
    install_summary(module)
    # Runtime order: leader wrapper is imported before dual-rank is installed.
    install_leaders(module)
    install_detail_display(module)
    install_dual_rank(module)
    return module


def mapping_file(tmp_path: Path) -> Path:
    path = tmp_path / "themes.json"
    path.write_text(
        json.dumps(
            {
                "master_version": "LEADER_TEST",
                "themes": [
                    {
                        "theme_id": "T1",
                        "theme_name": "테스트테마",
                        "members": ["000001", "000002", "000003"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def row(
    code: str,
    *,
    candidate: float,
    rate: float,
    ratio: float,
    one: float,
    five: float,
    execution: float,
    strength5: float,
    program: float,
    large: float,
    blocked: list[str] | None = None,
) -> dict:
    result = {
        "stock_code": code,
        "stock_name": code,
        "price": 1000.0,
        "change_rate": rate,
        "trade_value_eok": five * 10,
        "trade_value_1m_eok": one,
        "trade_value_5m_eok": five,
        "amount_ratio": ratio,
        "candidate_score": candidate,
        "candidate_grade": "A" if candidate >= 90 else "F",
        "execution_strength": execution,
        "strength_5m": strength5,
        "program_net": program,
        "large_trade_net_sum_eok": large,
        "large_trade_net_count": 1,
    }
    if blocked:
        result["metric_scoring_blocked_groups"] = blocked
    return result


def test_theme_leader_ignores_candidate_score_and_uses_momentum_money(tmp_path):
    module = isolated_engine()
    loader = module.ThemeMembershipLoader(paths=[mapping_file(tmp_path)], refresh_sec=1)
    builder = module.ThemeProjectionBuilder(loader=loader)
    rows = (
        row(
            "000001",
            candidate=100,
            rate=1,
            ratio=0.5,
            one=1,
            five=5,
            execution=90,
            strength5=90,
            program=-10,
            large=-1,
        ),
        row(
            "000002",
            candidate=10,
            rate=5,
            ratio=5,
            one=20,
            five=50,
            execution=150,
            strength5=140,
            program=10,
            large=5,
        ),
        row(
            "000003",
            candidate=95,
            rate=4,
            ratio=4,
            one=15,
            five=40,
            execution=250,
            strength5=200,
            program=100,
            large=50,
            blocked=["execution", "strength5", "program", "large_trade"],
        ),
    )

    first = builder(1, rows, {"snapshot_epoch": 1000.0})
    leaders = first["rows"][0]["leaders"]

    assert leaders[0]["stock_code"] == "000002"
    assert leaders[0]["leadership_role"] == "주도"
    assert leaders[0]["leadership_score"] > leaders[1]["leadership_score"]
    assert first["leader_selection_status"]["candidate_score_used"] is False
    assert first["policy"]["theme_leader_candidate_score_used"] is False
    assert first["performance_breakdown"]["leader_rank_ms"] >= 0

    rows_2 = list(rows)
    rows_2[1] = dict(rows_2[1], change_rate=6.0)
    second = builder(2, tuple(rows_2), {"snapshot_epoch": 1061.0})
    second_leader = second["rows"][0]["leaders"][0]
    assert second_leader["stock_code"] == "000002"
    assert second_leader["stock_change_momentum_1m"] == 1.0


def test_selected_detail_is_server_sorted_by_leader_score(tmp_path):
    module = isolated_engine()
    loader = module.ThemeMembershipLoader(paths=[mapping_file(tmp_path)], refresh_sec=1)
    builder = module.ThemeProjectionBuilder(loader=loader)
    rows = (
        row(
            "000001",
            candidate=100,
            rate=1,
            ratio=0.5,
            one=1,
            five=5,
            execution=90,
            strength5=90,
            program=-10,
            large=-1,
        ),
        row(
            "000002",
            candidate=10,
            rate=5,
            ratio=5,
            one=20,
            five=50,
            execution=150,
            strength5=140,
            program=10,
            large=5,
        ),
        row(
            "000003",
            candidate=20,
            rate=-1,
            ratio=1,
            one=2,
            five=8,
            execution=80,
            strength5=80,
            program=-1,
            large=-1,
        ),
    )
    builder(1, rows, {"snapshot_epoch": 1000.0})

    payload = builder.build_selected_detail(1, "T1", rows, {})
    members = payload["theme"]["members"]

    assert members[0]["stock_code"] == "000002"
    assert members[0]["leadership_role"] == "주도"
    assert members[0]["candidate_score_text"] == members[0]["leadership_score_text"]
    assert members[0]["stockboard_candidate_score"] == 10
    assert members[0]["stockboard_candidate_score_text"] == "10.0"
    assert payload["policy"]["candidate_score_used_for_leader"] is False
    assert payload["policy"]["browser_leader_sort_allowed"] is False
    assert payload["policy"]["detail_score_display"] == "leadership_score"


def test_theme_leader_modules_have_no_openapi_tr_or_browser_sort():
    for path in (
        ROOT / "realtime_v2" / "theme_leader_selection_patch.py",
        ROOT / "realtime_v2" / "theme_leader_detail_display_patch.py",
    ):
        source = path.read_text(encoding="utf-8")
        for forbidden in (
            "dynamicCall",
            "CommRqData",
            "SetRealReg",
            "QAxWidget",
            "kiwoom_data_provider",
        ):
            assert forbidden not in source
    source = (
        ROOT / "realtime_v2" / "theme_leader_selection_patch.py"
    ).read_text(encoding="utf-8")
    assert "candidate_score_used" in source
    assert "summary_extra_member_passes\": 1" in source
    assert "price30_momentum25_amount20_recent_money15_strength_flow10" in source
