from __future__ import annotations

import copy
import json
from pathlib import Path

from realtime_v2.trade_value_rank_score_patch import trade_value_rank_points
from stockboard_candidate_config import (
    grade_text_for_percent,
    load_candidate_model_config,
    load_candidate_model_registry,
    validate_candidate_model_config,
)
from stockboard_candidate_engine import ConfigDrivenCandidateRankingEngine
from stockboard_candidate_features import FeatureSnapshot, linear_rank_points

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "candidate_models" / "FIVE_FACTOR_FLOW_V01.json"
REGISTRY_PATH = ROOT / "configs" / "candidate_models" / "_registry.json"


def test_registry_exposes_one_final_model_only():
    registry = load_candidate_model_registry(include_configs=True)
    assert registry["runtime_status"] == "single_final_model_json_v1"
    assert len(registry["models"]) == 1
    assert registry["invalid_models"] == []
    assert set(registry["configs"]) == {registry["default_model_id"]}


def test_exact_points_and_grade_boundaries_come_from_json():
    config = load_candidate_model_config()
    policy = config["feature_policies"]["trade_value_rank"]
    bands = config["grade_bands"]
    expected = {
        1: (100.0, "A100"),
        2: (99.0, "A99"),
        11: (90.0, "A90"),
        12: (89.0, "B89"),
        21: (80.0, "B80"),
        22: (79.0, "C79"),
        100: (1.0, "F1"),
        101: (0.0, "F0"),
    }
    for rank, (points, grade) in expected.items():
        assert linear_rank_points(rank, policy) == points
        assert trade_value_rank_points(rank) == points
        assert grade_text_for_percent(points, bands) == grade


def test_feature_snapshot_uses_model_policy_without_runtime_patch():
    config = load_candidate_model_config()
    rows = [{"rank": rank, "stock_code": f"{rank:06d}"} for rank in range(1, 103)]
    snapshot = FeatureSnapshot(rows, config["feature_policies"])
    assert snapshot.get(0, "trade_value_rank").points == 100.0
    assert snapshot.get(99, "trade_value_rank").points == 1.0
    assert snapshot.get(100, "trade_value_rank").points == 0.0
    assert snapshot.get(0, "trade_value_rank").source == "candidate_model_json"


def test_changing_json_policy_copy_changes_score_without_code_change():
    config = copy.deepcopy(load_candidate_model_config())
    policy = config["feature_policies"]["trade_value_rank"]
    policy.update({"start_score": 50, "end_score": 5, "outside_score": 2})
    config["grade_bands"] = [
        {"grade": "S", "class": "s", "min_score": 45},
        {"grade": "F", "class": "f", "min_score": 0},
    ]
    assert validate_candidate_model_config(config) == []
    engine = ConfigDrivenCandidateRankingEngine(config)
    output = engine.enrich([
        {"stock_code": "000001", "rank": 1},
        {"stock_code": "000100", "rank": 100},
        {"stock_code": "000101", "rank": 101},
    ])
    by_code = {row["stock_code"]: row for row in output}
    assert by_code["000001"]["grade_score"] == 50.0
    assert by_code["000001"]["candidate_grade_text"] == "S50"
    assert by_code["000100"]["grade_score"] == 5.0
    assert by_code["000101"]["grade_score"] == 2.0


def test_final_config_contains_formula_grade_and_all_score_groups():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert config["feature_policies"]["trade_value_rank"]["type"] == "linear_rank"
    assert config["grade_bands"][-1]["min_score"] == 0
    for group in ("final_score", "entry_score", "confirmation_score", "focus_score"):
        assert config["score_structure"][group] == [{"key": "trade_value_rank", "weight": 100}]
    assert len(registry["models"]) == 1


def test_runtime_rows_expose_config_provenance():
    row = ConfigDrivenCandidateRankingEngine(load_candidate_model_config()).enrich(
        [{"stock_code": "000001", "rank": 1}]
    )[0]
    assert row["candidate_config_id"] == "FIVE_FACTOR_FLOW_V01"
    assert row["candidate_config_schema_version"] == 6
    assert len(row["candidate_config_hash"]) == 64
    assert row["candidate_config_validation_status"] == "READY"
