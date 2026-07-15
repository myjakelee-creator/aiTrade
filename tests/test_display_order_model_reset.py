from __future__ import annotations

import ast
from pathlib import Path

import stockboard_display_order
from realtime_v2.display_order_model_reset_patch import POLICY


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "display_order_model_reset_patch.py"


def rows(order: list[int], model_id: str):
    return [
        {
            "stock_code": f"{value:06d}",
            "candidate_model_id": model_id,
            "candidate_score": 101 - index,
            "grade_score": 101 - index,
            "selection_rank": index,
            "model_rank": index,
        }
        for index, value in enumerate(order, start=1)
    ]


def controller():
    return stockboard_display_order.DisplayOrderController(
        top_limit=20,
        challenger_hold_sec=0,
        strong_challenger_hold_sec=0,
        incumbent_out_hold_sec=0,
        normal_out_rank=21,
        swap_cooldown_sec=0,
        warm_limit=50,
        warm_out_rank=51,
        warm_challenger_hold_sec=0,
        warm_incumbent_out_hold_sec=0,
        warm_swap_cooldown_sec=0,
    )


def codes(items):
    return [item["stock_code"] for item in items]


def test_patch_is_valid_python_and_has_no_io():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in ("dynamicCall", "QAxWidget", "requests.", "socket", "Thread", "EventSource"):
        assert forbidden not in source


def test_same_model_keeps_stable_lanes():
    ctl = controller()
    first = ctl.apply(rows(list(range(1, 61)), "MODEL_A"))
    original_hot = codes(first[:20])

    second = ctl.apply(rows(list(range(20, 0, -1)) + list(range(21, 61)), "MODEL_A"))
    assert codes(second[:20]) == original_hot
    assert ctl.status()["candidate_model_reset_count"] == 0


def test_model_change_reinitialises_all_lanes_immediately():
    ctl = controller()
    ctl.apply(rows(list(range(1, 61)), "MODEL_A"))

    model_b_order = list(range(60, 0, -1))
    result = ctl.apply(rows(model_b_order, "MODEL_B"))

    assert codes(result[:20]) == [f"{value:06d}" for value in model_b_order[:20]]
    status = ctl.status()
    assert status["active_candidate_model_id"] == "MODEL_B"
    assert status["candidate_model_reset_count"] == 1
    assert status["candidate_model_reset_policy"] == POLICY
