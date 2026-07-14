from __future__ import annotations

import ast
from pathlib import Path

import stockboard_display_order
from realtime_v2.three_lane_display_order_patch import POLICY


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "three_lane_display_order_patch.py"


def make_rows(order: list[int]):
    return [
        {
            "stock_code": f"{value:06d}",
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


def codes(rows):
    return [row["stock_code"] for row in rows]


def test_patch_is_valid_python_and_adds_no_io_or_threads():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in ("dynamicCall", "QAxWidget", "requests.", "socket", "Thread", "EventSource"):
        assert forbidden not in source


def test_initial_lanes_are_hot20_warm30_and_cold_remainder():
    ctl = controller()
    result = ctl.apply(make_rows(list(range(1, 101))))

    assert len(result) == 100
    assert all(row["active_lane"] == "hot" for row in result[:20])
    assert all(row["active_lane"] == "warm" for row in result[20:50])
    assert all(row["active_lane"] == "cold" for row in result[50:])
    assert all(row["update_priority"] == "fast" for row in result[:20])
    assert all(row["update_priority"] == "warm" for row in result[20:50])
    assert all(row["update_priority"] == "cold" for row in result[50:])

    status = ctl.status()
    assert status["mode"] == POLICY
    assert status["top20_count"] == 20
    assert status["warm_count"] == 30
    assert status["cold_count"] == 50


def test_rank_changes_inside_a_lane_do_not_move_rows():
    ctl = controller()
    original = ctl.apply(make_rows(list(range(1, 101))))
    original_hot = codes(original[:20])
    original_warm = codes(original[20:50])

    reordered = list(range(20, 0, -1)) + list(range(50, 20, -1)) + list(range(51, 101))
    result = ctl.apply(make_rows(reordered))

    assert codes(result[:20]) == original_hot
    assert codes(result[20:50]) == original_warm


def test_pool_to_hot_transition_swaps_only_one_guarded_pair():
    ctl = controller()
    ctl.apply(make_rows(list(range(1, 61))))

    # Code 21 becomes score rank 1; incumbent code 20 falls to rank 60.
    changed = [21, *range(1, 20), *range(22, 61), 20]
    result = ctl.apply(make_rows(changed))

    hot_codes = set(codes(result[:20]))
    assert "000021" in hot_codes
    assert "000020" not in hot_codes
    assert ctl.status()["swap_count"] == 1


def test_cold_to_warm_transition_uses_the_warm_boundary_guard():
    ctl = controller()
    ctl.apply(make_rows(list(range(1, 61))))

    # HOT membership stays unchanged. Code 51 enters target rank 21 and code 50
    # falls outside the warm boundary.
    changed = [*range(1, 21), 51, *range(21, 50), *range(52, 61), 50]
    result = ctl.apply(make_rows(changed))

    warm_codes = set(codes(result[20:50]))
    cold_codes = set(codes(result[50:]))
    assert "000051" in warm_codes
    assert "000050" in cold_codes
    assert ctl.status()["warm_swap_count"] == 1


def test_rows_expose_target_and_active_lane_without_new_columns():
    ctl = controller()
    result = ctl.apply(make_rows(list(range(1, 101))))

    assert all("target_lane" in row for row in result)
    assert all("active_lane" in row for row in result)
    assert all("display_slot" in row for row in result)
    assert all("lane_pending" in row for row in result)
