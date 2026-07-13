from __future__ import annotations

from pathlib import Path

from realtime_v2.strategy_projection_engine import StrategyProjectionBuilder


def _row(
    code: str,
    *,
    status: str,
    score: float,
    model_rank: int,
    is_candidate: bool = False,
    candidate_rank=None,
    coverage: float = 1.0,
    missing=None,
):
    return {
        "stock_code": code,
        "stock_name": f"name-{code}",
        "price": 1000 + model_rank,
        "change_rate": score / 100,
        "trade_value_eok": 100 - model_rank,
        "candidate_model_id": "model-a",
        "candidate_model_name": "모델 A",
        "candidate_score": score,
        "candidate_grade": "A" if score >= 90 else "B",
        "candidate_status": status,
        "candidate_score_coverage": coverage,
        "entry_score": score - 3,
        "confirmation_score": score - 2,
        "focus_score": score - 1,
        "model_rank": model_rank,
        "candidate_rank": candidate_rank,
        "is_candidate": is_candidate,
        "required_feature_missing": list(missing or []),
        "grade_guard_failures": [],
        "momentum": "기존 모멘텀",
        "candidate_reason": "기존 후보 사유",
    }


def test_strategy_projection_classifies_existing_candidate_results_only():
    builder = StrategyProjectionBuilder(max_rows=10)
    payload = builder(
        17,
        (
            _row(
                "000001",
                status="READY",
                score=92,
                model_rank=1,
                is_candidate=True,
                candidate_rank=1,
            ),
            _row("000002", status="READY", score=85, model_rank=2),
            _row("000003", status="WATCH", score=75, model_rank=3),
            _row(
                "000004",
                status="WAIT_DATA",
                score=58,
                model_rank=4,
                coverage=0.50,
                missing=["program_net"],
            ),
            _row("000005", status="WEAK", score=45, model_rank=5),
        ),
        {},
    )

    assert payload["status"] == "READY"
    assert payload["input_feature_version"] == 17
    assert payload["selected_candidate_model_id"] == "model-a"
    assert payload["feature_row_count"] == 5
    assert payload["row_count"] == 5
    assert payload["lane_counts"] == {
        "focus": 1,
        "ready": 1,
        "watch": 1,
        "wait_data": 1,
        "weak": 1,
    }
    assert [row["strategy_lane"] for row in payload["rows"]] == [
        "focus",
        "ready",
        "watch",
        "wait_data",
        "weak",
    ]

    focus = payload["rows"][0]
    assert focus["candidate_score"] == 92
    assert focus["focus_score"] == 91
    assert focus["candidate_reason"] == "기존 후보 사유"
    assert focus["strategy_rank"] == 1
    assert focus["strategy_lane_rank"] == 1
    assert focus["order_side"] is None
    assert focus["auto_order_allowed"] is False

    policy = payload["policy"]
    assert policy["direct_tr_allowed"] is False
    assert policy["feature_recalculation_allowed"] is False
    assert policy["candidate_rescore_allowed"] is False
    assert policy["selected_candidate_model_only"] is True
    assert policy["auto_order_allowed"] is False


def test_strategy_projection_limit_does_not_change_internal_lane_counts():
    builder = StrategyProjectionBuilder(max_rows=2)
    payload = builder(
        3,
        (
            _row("000001", status="READY", score=90, model_rank=1, is_candidate=True),
            _row("000002", status="READY", score=85, model_rank=2),
            _row("000003", status="WATCH", score=75, model_rank=3),
        ),
        {},
    )

    assert payload["feature_row_count"] == 3
    assert payload["row_count"] == 2
    assert payload["lane_counts"]["focus"] == 1
    assert payload["lane_counts"]["ready"] == 1
    assert payload["lane_counts"]["watch"] == 1
    assert payload["output_lane_counts"]["watch"] == 0


def test_strategy_projection_waits_without_shared_rows():
    payload = StrategyProjectionBuilder(max_rows=10)(9, tuple(), {})
    assert payload["status"] == "WAIT_FEATURE_ROWS"
    assert payload["row_count"] == 0
    assert payload["policy"]["input"] == "board_data_hub_shared_feature_snapshot"


def test_strategy_projection_contains_no_tr_or_candidate_rescore_path():
    source = Path("realtime_v2/strategy_projection_engine.py").read_text(
        encoding="utf-8"
    )
    assert "kiwoom_data_provider" not in source
    assert "dynamicCall" not in source
    assert "coordinator.execute" not in source
    assert "stockboard_candidate_engine" not in source
    assert "from stockboard_candidate_features import FeatureSnapshot" not in source
    assert "LatestOnlyProjectionWorker" in source
