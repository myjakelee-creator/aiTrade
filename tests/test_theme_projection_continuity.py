from __future__ import annotations

import json
from pathlib import Path

from realtime_v2 import theme_projection_engine as theme_module
from realtime_v2.theme_projection_continuity_guard_patch import install


ROOT = Path(__file__).resolve().parents[1]


def _builder(tmp_path: Path):
    mapping = tmp_path / "theme.json"
    mapping.write_text(
        json.dumps(
            {
                "themes": [
                    {
                        "theme_id": "test",
                        "theme_name": "테스트",
                        "members": ["005930"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    install(theme_module)
    return theme_module.ThemeProjectionBuilder(
        theme_module.ThemeMembershipLoader(paths=[mapping], refresh_sec=1)
    )


def _row(*, blocked: bool) -> dict:
    row = {
        "stock_code": "005930",
        "stock_name": "삼성전자",
        "candidate_score": 50.0,
        "candidate_grade": "F",
        "change_rate": 1.0,
        "trade_value_eok": 1000.0,
        "amount_ratio": 1.0,
        "execution_strength": 200.0,
        "strength_5m": 150.0,
        "program_net": 50.0,
        "large_trade_net_sum_eok": 10.0,
        "large_trade_net_count": 5,
        "metric_continuity_basis": (
            "previous_session_hold" if blocked else "current_session"
        ),
        "metric_continuity_reference_date": "20260713",
    }
    if blocked:
        row["metric_scoring_blocked_groups"] = [
            "execution",
            "strength5",
            "program",
            "large_trade",
        ]
    return row


def test_held_values_remain_visible_but_are_excluded_from_theme_score(tmp_path):
    builder = _builder(tmp_path)

    current = builder(1, (_row(blocked=False),), {})
    held = builder(2, (_row(blocked=True),), {})

    current_theme = current["details"]["test"]
    held_theme = held["details"]["test"]
    held_member = held_theme["members"][0]

    assert held_member["execution_strength"] == 200.0
    assert held_member["program_net"] == 50.0
    assert held_member["large_trade_net_sum_eok"] == 10.0
    assert held_member["metric_basis_text"] == "전일값 유지·점수 제외"
    assert held_theme["program_net_eok"] == 50.0
    assert held_theme["large_trade_net_eok"] == 10.0
    assert held_theme["held_member_count"] == 1
    assert held_theme["score"] < current_theme["score"]
    assert held["policy"]["previous_session_metric_scoring_allowed"] is False
    assert not any(key.startswith("_theme_score_") for key in held_member)


def test_continuity_is_installed_before_hub_projection_runtimes_start():
    hub_source = (
        ROOT / "realtime_v2" / "worker_board_data_hub_patch.py"
    ).read_text(encoding="utf-8")
    flow_source = (
        ROOT / "realtime_v2" / "theme_projection_flow_history_patch.py"
    ).read_text(encoding="utf-8")

    assert "install_theme_projection_flow_history(theme_projection_module)" in hub_source
    assert "install_board_metric_continuity(worker_base)" in flow_source
    assert "install_theme_projection_continuity_guard(theme_module)" in flow_source
    assert "FLOW_HOLD_PATH" in flow_source
    assert "persist_until_next_actual_premarket" in flow_source


def test_theme_continuity_modules_have_no_tr_qax_or_browser_calculation_path():
    for path in (
        ROOT / "realtime_v2" / "board_metric_continuity_patch.py",
        ROOT / "realtime_v2" / "theme_projection_continuity_guard_patch.py",
        ROOT / "realtime_v2" / "theme_projection_flow_history_patch.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert "dynamicCall" not in source
        assert "QAxWidget" not in source
        assert "kiwoom_data_provider" not in source
        assert "coordinator.execute" not in source
