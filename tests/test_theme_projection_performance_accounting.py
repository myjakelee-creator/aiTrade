from __future__ import annotations

from types import SimpleNamespace

from realtime_v2.theme_projection_performance_accounting_patch import install


class DummyBuilder:
    def __call__(self, _feature_version, _rows, _meta):
        return {
            "calculate_ms": 50.0,
            "performance_breakdown": {
                "total_ms": 50.0,
                "aggregate_ms": 14.0,
                "score_sort_ms": 1.0,
                "momentum_ms": 0.2,
                "dual_rank_ms": 4.0,
                "leader_rank_ms": 12.0,
                # Simulate the old outer-wrapper double count.
                "other_ms": 30.8,
            },
        }


def test_final_accounting_subtracts_named_nonoverlapping_phases_once():
    module = SimpleNamespace(ThemeProjectionBuilder=DummyBuilder)
    install(module)
    payload = module.ThemeProjectionBuilder()(1, tuple(), {})
    performance = payload["performance_breakdown"]

    assert performance["accounted_ms"] == 31.2
    assert performance["other_ms"] == 18.8
    assert (
        performance["accounting_policy"]
        == "final_total_minus_named_nonoverlapping_phases"
    )
