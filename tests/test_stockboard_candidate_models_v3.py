from __future__ import annotations

from stockboard_ranking_engine import (
    enrich_candidate_model_fields,
    load_candidate_model_config,
    load_candidate_model_registry,
    validate_candidate_model_config,
)


def test_only_final_json_model_is_registered_and_valid():
    registry = load_candidate_model_registry(include_configs=True)
    assert len(registry["models"]) == 1
    assert registry["invalid_models"] == []
    model_id = registry["default_model_id"]
    assert set(registry["configs"]) == {model_id}
    assert validate_candidate_model_config(registry["configs"][model_id]) == []


def test_stale_old_model_request_resolves_to_final_model_only():
    config = load_candidate_model_config("NET_BUY_STRENGTH_V02")
    assert config["id"] == "FIVE_FACTOR_FLOW_V01"
    assert config["name"] == "거래대금 순위 v0.1"
    assert config["requested_model_id"] == "NET_BUY_STRENGTH_V02"


def test_final_model_runs_unified_funnel():
    rows = [
        {"stock_code": f"{rank:06d}", "rank": rank}
        for rank in range(1, 121)
    ]
    output = enrich_candidate_model_fields(rows)
    assert len(output) == 120
    assert [row["model_rank"] for row in output] == list(range(1, 121))
    assert sum(row["pool_stage"] == "top20" for row in output) == 20
    assert sum(row["pool_stage"] == "top50" for row in output) == 30
    assert sum(bool(row["is_candidate"]) for row in output) == 5
    assert output[0]["candidate_grade_text"] == "A100"
    assert output[99]["candidate_grade_text"] == "F1"
    assert output[100]["candidate_grade_text"] == "F0"


def test_non_rank_fields_do_not_change_final_score():
    rows = [
        {
            "stock_code": "000001",
            "rank": 1,
            "change_rate": -30,
            "execution_strength": 0,
            "program_net": -999,
            "large_trade_net_sum_eok": -999,
        },
        {
            "stock_code": "000012",
            "rank": 12,
            "change_rate": 30,
            "execution_strength": 999,
            "program_net": 999,
            "large_trade_net_sum_eok": 999,
        },
    ]
    output = enrich_candidate_model_fields(rows)
    by_code = {row["stock_code"]: row for row in output}
    assert by_code["000001"]["grade_score"] == 100
    assert by_code["000012"]["grade_score"] == 89
