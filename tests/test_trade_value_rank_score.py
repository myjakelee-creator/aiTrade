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
from stockboard_candidate_features import FeatureSnapshot


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "trade_value_rank_score_patch.py"
CONFIG_PATH = ROOT / "configs" / "candidate_models" / "FIVE_FACTOR_FLOW_V01.json"
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


def test_five_factor_config_places_trade_value_first_and_keeps_total_weight_100():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    final_score = config["score_structure"]["final_score"]

    assert final_score[0] == {"key": "trade_value_rank", "weight": 20}
    assert sum(float(item["weight"]) for item in final_score if float(item["weight"]) > 0) == 100.0


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
