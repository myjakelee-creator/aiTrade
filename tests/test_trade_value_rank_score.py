from __future__ import annotations

import ast
import json
from pathlib import Path

from realtime_v2.trade_value_rank_score_patch import (
    POLICY,
    install,
    trade_value_rank_points,
)
from stockboard_candidate_config import grade_text_for_percent
from stockboard_candidate_engine import ConfigDrivenCandidateRankingEngine
from stockboard_candidate_features import FeatureSnapshot


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "trade_value_rank_score_patch.py"
CONFIG_PATH = ROOT / "configs" / "candidate_models" / "FIVE_FACTOR_FLOW_V01.json"
REGISTRY_PATH = ROOT / "configs" / "candidate_models" / "_registry.json"
INIT_PATH = ROOT / "realtime_v2" / "__init__.py"


def test_exact_top100_linear_points():
    expected = {
        1: 100.0,
        2: 99.0,
        11: 90.0,
        12: 89.0,
        21: 80.0,
        22: 79.0,
        100: 1.0,
        101: 0.0,
        182: 0.0,
        0: 0.0,
        None: 0.0,
    }
    for rank, points in expected.items():
        assert trade_value_rank_points(rank) == points


def test_requested_grade_boundaries_are_preserved():
    assert grade_text_for_percent(trade_value_rank_points(1)) == "A100"
    assert grade_text_for_percent(trade_value_rank_points(11)) == "A90"
    assert grade_text_for_percent(trade_value_rank_points(12)) == "B89"
    assert grade_text_for_percent(trade_value_rank_points(21)) == "B80"
    assert grade_text_for_percent(trade_value_rank_points(22)) == "C79"
    assert grade_text_for_percent(trade_value_rank_points(100)) == "F1"
    assert grade_text_for_percent(trade_value_rank_points(101)) == "F0"


def test_feature_snapshot_uses_absolute_top100_score_not_internal_universe_percentile():
    install()
    rows = [{"rank": rank, "stock_code": f"{rank:06d}"} for rank in range(1, 183)]
    snapshot = FeatureSnapshot(rows)

    first = snapshot.get(0, "trade_value_rank")
    hundredth = snapshot.get(99, "trade_value_rank")
    outside = snapshot.get(100, "trade_value_rank")

    assert first.points == 100.0
    assert hundredth.points == 1.0
    assert outside.points == 0.0
    assert first.source == POLICY
    assert hundredth.source == POLICY


def test_default_model_is_trade_value_rank_only_with_no_required_side_features():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    assert config["name"] == "거래대금 순위 v0.1"
    assert config["label"] == "거래대금 순위 v0.1"
    assert config["required_features"] == []
    for group_name in ("final_score", "entry_score", "confirmation_score", "focus_score"):
        assert config["score_structure"][group_name] == [
            {"key": "trade_value_rank", "weight": 100}
        ]

    default_entry = next(
        item for item in registry["models"] if item["id"] == registry["default_model_id"]
    )
    assert default_entry["label"] == "거래대금 순위 v0.1"


def test_final_candidate_score_equals_trade_value_rank_points_only():
    install()
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    engine = ConfigDrivenCandidateRankingEngine(config)
    rows = [
        {
            "stock_code": "000001",
            "rank": 1,
            "change_rate": -20,
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
        {"stock_code": "000100", "rank": 100},
        {"stock_code": "000101", "rank": 101},
    ]

    enriched = engine.enrich(rows)
    by_code = {row["stock_code"]: row for row in enriched}

    assert by_code["000001"]["grade_score"] == 100.0
    assert by_code["000001"]["candidate_grade_text"] == "A100"
    assert by_code["000012"]["grade_score"] == 89.0
    assert by_code["000012"]["candidate_grade_text"] == "B89"
    assert by_code["000100"]["grade_score"] == 1.0
    assert by_code["000100"]["candidate_grade_text"] == "F1"
    assert by_code["000101"]["grade_score"] == 0.0
    assert by_code["000101"]["candidate_grade_text"] == "F0"
    assert [row["rank"] for row in enriched] == [1, 12, 100, 101]


def test_patch_adds_no_collector_network_thread_or_browser_work():
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


def test_runtime_installs_trade_value_scoring_before_candidate_unification():
    source = INIT_PATH.read_text(encoding="utf-8")
    assert source.index("install_trade_value_rank_score()") < source.index(
        "install_candidate_score_unification()"
    )
