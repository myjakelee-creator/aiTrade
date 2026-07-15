from __future__ import annotations

import ast
import copy
from datetime import datetime
from pathlib import Path

import realtime_v2  # noqa: F401 - installs the production policy patch
from realtime_v2.candidate_json_eight_criteria_patch import CRITERIA_KEYS
from stockboard_candidate_config import (
    load_candidate_model_config,
    validate_candidate_model_config,
)
from stockboard_candidate_features import FeatureSnapshot


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "candidate_json_eight_criteria_patch.py"


def _rows() -> list[dict]:
    now = datetime.now().isoformat()
    return [
        {
            "stock_code": "000001",
            "rank": 1,
            "prev_rank": 100,
            "trade_value_eok": 1000,
            "prev_trade_value_eok": 100,
            "execution_strength": 200,
            "execution_strength_updated_at": now,
            "strength_5m": 200,
            "program_net": 100,
            "program_net_updated_at": now,
            "large_trade_buy_count": 10,
            "large_trade_sell_count": 1,
            "large_trade_buy_sum_eok": 10,
            "large_trade_sell_sum_eok": 1,
            "large_trade_net_sum_eok": 9,
        },
        {
            "stock_code": "000002",
            "rank": 50,
            "prev_rank": 80,
            "trade_value_eok": 500,
            "prev_trade_value_eok": 250,
            "execution_strength": 120,
            "execution_strength_updated_at": now,
            "strength_5m": 100,
            "program_net": 10,
            "program_net_updated_at": now,
            "large_trade_buy_count": 3,
            "large_trade_sell_count": 2,
            "large_trade_buy_sum_eok": 3,
            "large_trade_sell_sum_eok": 2,
            "large_trade_net_sum_eok": 1,
        },
        {
            "stock_code": "000101",
            "rank": 101,
            "prev_rank": 90,
            "trade_value_eok": 100,
            "prev_trade_value_eok": 200,
            "execution_strength": 70,
            "execution_strength_updated_at": now,
            "strength_5m": 70,
            "program_net": -1,
            "program_net_updated_at": now,
            "large_trade_buy_count": 0,
            "large_trade_sell_count": 1,
            "large_trade_buy_sum_eok": 0,
            "large_trade_sell_sum_eok": 1,
            "large_trade_net_sum_eok": -1,
        },
    ]


def test_final_json_declares_all_eight_criteria_but_activates_only_trade_rank():
    config = load_candidate_model_config()
    criteria = config["selection_criteria"]

    assert tuple(item["key"] for item in criteria) == CRITERIA_KEYS
    assert [(item["key"], item["weight"]) for item in criteria if item["enabled_in_final_score"]] == [
        ("trade_value_rank", 100)
    ]
    assert all(item["calculation_mode"] == "on_demand" for item in criteria)
    assert validate_candidate_model_config(config) == []


def test_trade_rank_only_final_model_does_not_prepare_other_metrics():
    config = load_candidate_model_config()
    snapshot = FeatureSnapshot(_rows(), config["feature_policies"])

    assert snapshot.json_policy_prepare_pass_count == 0
    assert snapshot.get(0, "trade_value_rank").points == 100
    assert snapshot.get(2, "trade_value_rank").points == 0
    assert snapshot.json_policy_prepare_pass_count == 0


def test_remaining_seven_criteria_are_json_driven_and_share_one_prepare_pass():
    config = load_candidate_model_config()
    snapshot = FeatureSnapshot(_rows(), config["feature_policies"])

    assert snapshot.get(0, "rank_gap").points > snapshot.get(1, "rank_gap").points
    assert snapshot.json_policy_prepare_pass_count == 0

    assert snapshot.get(0, "amount_ratio").points == 100
    assert snapshot.json_policy_prepare_pass_count == 1
    assert snapshot.get(0, "program_net").points == 100
    assert snapshot.get(0, "large_trade").points > snapshot.get(1, "large_trade").points
    assert snapshot.get(0, "instant_strength").points == 100
    assert snapshot.get(1, "five_min_strength").points == 55
    assert snapshot.get(0, "combination_quality").points >= 80
    assert snapshot.json_policy_prepare_pass_count == 1


def test_changing_json_policy_copy_changes_result_without_python_change():
    config = load_candidate_model_config()
    policies = copy.deepcopy(config["feature_policies"])
    policies["instant_strength"]["points"][-1]["score"] = 50

    snapshot = FeatureSnapshot(_rows(), policies)
    assert snapshot.get(0, "instant_strength").points == 50


def test_patch_adds_no_market_data_or_parallel_runtime_work():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "dynamicCall",
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "requests.",
        "socket.",
        "Thread(",
        "EventSource(",
        "fetch(",
    ):
        assert forbidden not in source

    contract = load_candidate_model_config()["performance_contract"]
    assert contract["new_openapi_calls"] == 0
    assert contract["new_fids"] == 0
    assert contract["new_threads"] == 0
    assert contract["browser_scoring"] is False
