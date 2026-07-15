from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import theme_projection_engine as engine
from realtime_v2.theme_projection_dual_rank_patch import install
from realtime_v2.worker_theme_dual_rank_ui_patch import _ordered_theme_ids


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


def module():
    return SimpleNamespace(
        ThemeProjectionBuilder=Builder,
        _grade=engine._grade,
    )


def theme(theme_id: str, average: float, momentum: float, money: float):
    return {
        "theme_id": theme_id,
        "theme_name": theme_id,
        "coverage_status": "READY",
        "coverage_pct": 100.0,
        "active_member_count": 3,
        "amount_ratio_member_count": 3,
        "avg_change_rate": average,
        "change_rate_text": f"{average:+.2f}%",
        "breadth_pct": 66.7,
        "theme_amount_ratio": money,
        "trade_value_1m_eok": money * 10,
        "trade_value_5m_eok": money * 50,
        "change_momentum_1m": momentum,
        "change_persistence_5m": momentum,
        "program_net_eok": 0.0,
        "large_trade_net_eok": 0.0,
        "held_member_count": 0,
        "leaders": [],
    }


def test_average_view_is_server_completed_and_uses_only_average_change_order():
    mod = module()
    install(mod)
    payload = mod.ThemeProjectionBuilder()(
        1,
        (
            theme("AVERAGE_FIRST", 5.0, -2.0, 0.1),
            theme("MOMENTUM_FIRST", 1.0, 5.0, 10.0),
            theme("AVERAGE_LAST", -1.0, 1.0, 2.0),
        ),
        {},
    )

    assert [row["theme_id"] for row in payload["average_rows"]] == [
        "AVERAGE_FIRST",
        "MOMENTUM_FIRST",
        "AVERAGE_LAST",
    ]
    assert payload["average_rows"][0]["average_display_rank"] == 1
    assert payload["ranking_views"]["available"][0] == "average"
    assert payload["ranking_policy"]["average"]["other_metrics_used"] is False
    assert payload["policy"]["average_theme_view"] == "server_avg_change_rate_desc"

    reversed_ids = _ordered_theme_ids(
        payload,
        view="average",
        key="current",
        direction="desc",
    )
    assert reversed_ids == ["AVERAGE_LAST", "MOMENTUM_FIRST", "AVERAGE_FIRST"]


def test_average_button_compact_summary_and_column_resize_contracts():
    root = Path(__file__).resolve().parents[1]
    source = (
        root / "realtime_v2" / "theme_average_view_layout_patch.py"
    ).read_text(encoding="utf-8")
    package_source = (root / "realtime_v2" / "__init__.py").read_text(
        encoding="utf-8"
    )
    selected_detail_source = (
        root / "realtime_v2" / "worker_theme_selected_detail_patch.py"
    ).read_text(encoding="utf-8")

    assert "themeViewAverage" in source
    assert "평균등락률" in source
    assert "theme-summary-compact" in source
    assert "summary-top-card" in source
    assert "themeColumnMinimize" in source
    assert "열 최소화" in source
    assert "theme-column-resizer" in source
    assert "dblclick" in source
    assert "localStorage.setItem(widthStorageKey(table)" in source
    assert "__tbMinimizeAllColumns" in source
    assert "average_rows" in source
    assert "install_runtime_wrappers()" in package_source

    explicit_call = selected_detail_source.index("install_runtime_wrappers()")
    rank_alias = selected_detail_source.index(
        "from realtime_v2.theme_projection_dual_rank_patch import"
    )
    ui_alias = selected_detail_source.index(
        "from realtime_v2.worker_theme_dual_rank_ui_patch import"
    )
    assert explicit_call < rank_alias
    assert explicit_call < ui_alias
    assert "theme_average_view_layout_extension_loaded" in selected_detail_source
    assert "THEMEBOARD_AVERAGE_VIEW_RESIZABLE_COLUMNS_20260714" in selected_detail_source

    for forbidden in (
        "CommRqData",
        "SetRealReg",
        "dynamicCall",
        "QAxWidget",
        "kiwoom_data_provider",
    ):
        assert forbidden not in source
