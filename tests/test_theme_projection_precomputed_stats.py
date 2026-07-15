from __future__ import annotations

from types import SimpleNamespace

from realtime_v2 import theme_projection_precomputed_stats_patch as patch


class DummyBuilder:
    def _member_row(self, row, member):
        return {
            "change_rate": row.get("change_rate"),
            "amount_ratio": row.get("amount_ratio"),
        }

    def _aggregate_theme(self, theme, by_code):
        active = []
        for member in theme.get("members") or []:
            row = by_code.get(member["stock_code"])
            if row is not None:
                active.append(self._member_row(row, member))
        return {
            "theme_id": theme["theme_id"],
            "active_member_count": len(active),
            "members": active,
        }


def test_theme_stats_are_precomputed_during_existing_aggregation_pass():
    module = SimpleNamespace(ThemeProjectionBuilder=DummyBuilder)
    patch.install(module)
    builder = module.ThemeProjectionBuilder()

    result = builder._aggregate_theme(
        {
            "theme_id": "T1",
            "members": [
                {"stock_code": "000001"},
                {"stock_code": "000002"},
                {"stock_code": "000003"},
            ],
        },
        {
            "000001": {"change_rate": 1.0, "amount_ratio": 2.0},
            "000002": {"change_rate": 3.0, "amount_ratio": 4.0},
            "000003": {"change_rate": 9.0, "amount_ratio": 100.0},
        },
    )

    assert result["median_change_rate"] == 3.0
    assert result["theme_amount_ratio"] == 4.0
    assert result["amount_ratio_member_count"] == 3
    assert result["amount_ratio_coverage_pct"] == 100.0
    assert result["theme_member_stats_basis"] == "single_aggregation_pass"


def test_precomputed_stats_patch_is_idempotent():
    module = SimpleNamespace(ThemeProjectionBuilder=DummyBuilder)
    patch.install(module)
    first_member = module.ThemeProjectionBuilder._member_row
    first_aggregate = module.ThemeProjectionBuilder._aggregate_theme
    patch.install(module)
    assert module.ThemeProjectionBuilder._member_row is first_member
    assert module.ThemeProjectionBuilder._aggregate_theme is first_aggregate
