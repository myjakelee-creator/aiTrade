from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.candidate_score_unification_patch import (
    POLICY,
    unified_apply_funnel,
)


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "candidate_score_unification_patch.py"


def engine():
    return SimpleNamespace(
        config={
            "funnel": {
                "top50": {"take": 50},
                "top20": {"take": 20},
                "top5": {"take": 5},
            }
        }
    )


def row(code: str, score: float, *, coverage: float = 1.0, source_rank: int = 1):
    return {
        "stock_code": code,
        "candidate_score": score,
        "grade_score": score,
        "score_percent": score,
        "candidate_score_coverage": coverage,
        "candidate_status": "READY",
        "_source_rank": source_rank,
        "entry_score": 100 - source_rank,
        "confirmation_score": source_rank,
        "focus_score": 50,
    }


def test_patch_is_valid_python_and_has_no_runtime_io():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in ("dynamicCall", "QAxWidget", "requests.", "socket", "EventSource", "Thread"):
        assert forbidden not in source


def test_grade_score_is_the_only_primary_candidate_order():
    rows = [
        row("000001", 72, source_rank=1),
        row("000002", 91, source_rank=200),
        row("000003", 83, source_rank=3),
    ]

    ordered = unified_apply_funnel(engine(), rows)

    assert [item["stock_code"] for item in ordered] == ["000002", "000003", "000001"]
    assert [item["grade_score"] for item in ordered] == [91, 83, 72]
    assert [item["model_rank"] for item in ordered] == [1, 2, 3]
    assert all(item["selection_order_policy"] == POLICY for item in ordered)


def test_score_fields_and_all_compatibility_ranks_are_unified():
    ordered = unified_apply_funnel(
        engine(),
        [row(f"{index:06d}", 100 - index, source_rank=index) for index in range(1, 61)],
    )

    for index, item in enumerate(ordered, start=1):
        assert item["candidate_score"] == item["grade_score"] == item["score_percent"]
        assert item["selection_rank"] == item["model_rank"] == item["pool_rank"] == index
        assert item["entry_rank"] == item["confirmation_rank"] == item["focus_rank"] == index

    assert all(item["target_lane"] == "hot" for item in ordered[:20])
    assert all(item["target_lane"] == "warm" for item in ordered[20:50])
    assert all(item["target_lane"] == "cold" for item in ordered[50:])


def test_ties_use_coverage_then_trade_value_source_rank_only():
    rows = [
        row("000003", 80, coverage=0.80, source_rank=1),
        row("000002", 80, coverage=1.00, source_rank=20),
        row("000001", 80, coverage=1.00, source_rank=5),
    ]

    ordered = unified_apply_funnel(engine(), rows)
    assert [item["stock_code"] for item in ordered] == ["000001", "000002", "000003"]
