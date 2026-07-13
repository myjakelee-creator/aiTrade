from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import theme_projection_engine as engine
from realtime_v2.theme_projection_dual_rank_patch import install


class DummyBuilder:
    def __init__(self) -> None:
        pass

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


def module():
    return SimpleNamespace(
        ThemeProjectionBuilder=DummyBuilder,
        _rank_percent=engine._rank_percent,
        _grade=engine._grade,
    )


def theme(
    theme_id: str,
    *,
    average: float,
    breadth: float,
    ratio: float,
    one: float,
    five: float,
    momentum: float,
    persistence: float,
    candidate: float,
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
        "avg_change_rate": average,
        "breadth_pct": breadth,
        "theme_amount_ratio": ratio,
        "trade_value_1m_eok": one,
        "trade_value_5m_eok": five,
        "change_momentum_1m": momentum,
        "change_persistence_5m": persistence,
        "avg_candidate_score": candidate,
        "program_net_eok": program,
        "large_trade_net_eok": large,
        "held_member_count": 0,
        "leaders": [],
    }


def test_momentum_is_default_and_negative_huge_money_theme_is_capped():
    mod = module()
    install(mod)
    builder = mod.ThemeProjectionBuilder()
    negative_huge = theme(
        "NEGATIVE_HUGE",
        average=-11.0,
        breadth=0.0,
        ratio=8.0,
        one=1000.0,
        five=5000.0,
        momentum=-1.0,
        persistence=-3.0,
        candidate=99.0,
        program=100.0,
        large=20.0,
    )
    positive_broad = theme(
        "POSITIVE_BROAD",
        average=4.0,
        breadth=100.0,
        ratio=2.0,
        one=100.0,
        five=300.0,
        momentum=0.5,
        persistence=1.2,
        candidate=10.0,
        program=1.0,
        large=0.1,
    )

    payload = builder(1, (negative_huge, positive_broad), {})

    assert payload["rows"][0]["theme_id"] == "POSITIVE_BROAD"
    assert payload["money_rows"][0]["theme_id"] == "NEGATIVE_HUGE"
    negative = next(row for row in payload["rows"] if row["theme_id"] == "NEGATIVE_HUGE")
    assert negative["trend_score"] <= 59.0
    assert negative["trend_grade"] == "F"
    assert payload["ranking_views"]["default"] == "momentum"
    assert payload["policy"]["browser_sort_allowed"] is False


def test_average_candidate_score_is_not_used_and_amount_ratio_is_once_per_view():
    mod = module()
    install(mod)
    builder = mod.ThemeProjectionBuilder()
    first = theme(
        "FIRST",
        average=2.0,
        breadth=80.0,
        ratio=3.0,
        one=50.0,
        five=200.0,
        momentum=0.2,
        persistence=0.5,
        candidate=0.0,
    )
    second = dict(first)
    second["theme_id"] = "SECOND"
    second["theme_name"] = "SECOND"
    second["avg_candidate_score"] = 100.0

    payload = builder(1, (first, second), {})
    by_id = {row["theme_id"]: row for row in payload["rows"]}

    assert by_id["FIRST"]["trend_score"] == by_id["SECOND"]["trend_score"]
    assert by_id["FIRST"]["money_score"] == by_id["SECOND"]["money_score"]
    assert payload["ranking_policy"]["average_candidate_score_used"] is False
    assert payload["ranking_policy"]["amount_ratio_applied_once_per_view"] is True
    assert payload["performance_breakdown"]["dual_rank_ms"] >= 0


def test_dual_rank_and_ui_modules_have_no_tr_or_browser_sort_path():
    root = Path(__file__).resolve().parents[1]
    rank_source = (
        root / "realtime_v2" / "theme_projection_dual_rank_patch.py"
    ).read_text(encoding="utf-8")
    ui_source = (
        root / "realtime_v2" / "worker_theme_dual_rank_ui_patch.py"
    ).read_text(encoding="utf-8")
    worker_source = (
        root / "realtime_v2" / "worker_theme_selected_detail_patch.py"
    ).read_text(encoding="utf-8")

    for source in (rank_source, ui_source):
        for forbidden in (
            "dynamicCall",
            "CommRqData",
            "SetRealReg",
            "QAxWidget",
            "kiwoom_data_provider",
        ):
            assert forbidden not in source

    assert '"money_rows"' in rank_source
    assert '"default": "momentum"' in rank_source
    assert "payload.money_rows" in ui_source
    assert ".sort(" not in ui_source
    assert "themeViewMomentum" in ui_source
    assert "themeViewMoney" in ui_source
    assert "install_theme_dual_rank(theme_projection_module)" in worker_source
    assert "install_theme_dual_rank_ui(base)" in worker_source


def test_theme_cards_use_color_cues_top10_and_detail_below_selected_card_row():
    root = Path(__file__).resolve().parents[1]
    ui_source = (
        root / "realtime_v2" / "worker_theme_dual_rank_ui_patch.py"
    ).read_text(encoding="utf-8")

    assert "const cardList=list.slice(0,10);" in ui_source
    assert 'class="${tone(theme.change_rate_tone)}"' in ui_source
    assert "leader-rate ${rateTone}" in ui_source
    assert "item.change_rate_text" in ui_source
    assert "function __tbPlaceDetailBelowSelectedCardRow()" in ui_source
    assert "function __tbGridColumnCount(radar)" in ui_source
    assert "cards[rowEndIndex].insertAdjacentElement('afterend',detail);" in ui_source
    assert "__tbDetachDetailBeforeRadarRender();" in ui_source
    assert "grid-column:1/-1" in ui_source
    assert "theme-list-layout" in ui_source
    assert "전체 테마는 하단 순위표" in ui_source
