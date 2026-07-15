from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.theme_projection_continuity_guard_patch import install


class DummyBuilder:
    def __init__(self) -> None:
        self.member_projection_count = 0

    def _member_row(self, row, member):
        self.member_projection_count += 1
        return {
            "stock_code": row["stock_code"],
            "master_weight": 1.0,
            "candidate_score": 50.0,
            "change_rate": 1.0,
            "amount_ratio": 1.0,
            "execution_strength": 200.0,
            "program_net": 50.0,
            "large_trade_net_sum_eok": 10.0,
            "large_trade_net_count": 5,
            "leadership_score": 90.0,
        }

    def _aggregate_theme(self, theme, by_code):
        member = self._member_row(by_code["005930"], {"stock_code": "005930"})
        return {
            "theme_id": "T1",
            "program_net_eok": member["program_net"],
            "large_trade_net_eok": member["large_trade_net_sum_eok"],
            "members": [member],
        }

    def _score_themes(self, themes):
        for theme in themes:
            theme["score"] = 100.0 if theme.get("program_net_eok") is not None else 50.0

    def __call__(self, feature_version, rows, meta):
        theme = self._aggregate_theme({"theme_id": "T1"}, {"005930": rows[0]})
        self._score_themes([theme])
        return {
            "status": "READY",
            "rows": [dict(theme)],
            "details": {"T1": theme},
            "policy": {},
        }


def test_blocked_metric_row_is_projected_only_once():
    module = SimpleNamespace(ThemeProjectionBuilder=DummyBuilder)
    install(module)
    builder = module.ThemeProjectionBuilder()

    payload = builder(
        1,
        (
            {
                "stock_code": "005930",
                "metric_continuity_basis": "previous_session_hold",
                "metric_scoring_blocked_groups": [
                    "execution",
                    "program",
                    "large_trade",
                ],
            },
        ),
        {},
    )

    member = payload["details"]["T1"]["members"][0]
    assert builder.member_projection_count == 1
    assert member["execution_strength"] == 200.0
    assert member["program_net"] == 50.0
    assert member["large_trade_net_sum_eok"] == 10.0
    assert member["leadership_score"] < 90.0
    assert payload["continuity_guard_status"]["member_reprojection_count"] == 0
    assert payload["policy"]["continuity_member_reprojection_allowed"] is False
