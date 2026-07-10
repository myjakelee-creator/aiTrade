from __future__ import annotations

from stockboard_display_order import DisplayOrderController


def _rows(order, scores=None):
    scores = scores or {}
    return [
        {
            "stock_code": f"{code:06d}",
            "candidate_score": scores.get(code, 80),
            "model_rank": index,
        }
        for index, code in enumerate(order, start=1)
    ]


def test_internal_positions_stay_fixed_when_scores_reorder():
    controller = DisplayOrderController(
        challenger_hold_sec=999,
        strong_challenger_hold_sec=999,
        incumbent_out_hold_sec=999,
    )
    first = controller.apply(_rows(list(range(1, 31))))
    second = controller.apply(_rows(list(reversed(range(1, 31)))))

    assert [row["stock_code"] for row in second] == [
        row["stock_code"] for row in first
    ]
    assert controller.status()["paused"] is True
    assert controller.status()["mode"] == "lane_stable"


def test_only_guarded_top20_top300_swap_moves_rows():
    controller = DisplayOrderController(
        challenger_hold_sec=0,
        strong_challenger_hold_sec=0,
        incumbent_out_hold_sec=0,
        strong_margin=8,
        swap_cooldown_sec=0,
    )
    controller.apply(_rows(list(range(1, 31))))

    next_order = [21, *range(2, 21), 1, *range(22, 31)]
    result = controller.apply(_rows(next_order, scores={21: 95, 1: 70}))

    assert result[0]["stock_code"] == "000021"
    assert result[20]["stock_code"] == "000001"
    assert [row["stock_code"] for row in result[1:20]] == [
        f"{code:06d}" for code in range(2, 21)
    ]
    assert controller.status()["swap_count"] == 1


def test_previous_day_file_strength_is_blank_after_restart():
    controller = DisplayOrderController()
    row = {
        "stock_code": "000001",
        "candidate_score": 80,
        "strength_5m": 123.4,
        "strength_snapshot_at": "2000-01-01T15:00:00",
        "previous_daily_display_fallback": True,
    }
    result = controller.apply([row])[0]

    assert "strength_5m" not in result
    assert result["strength_status"] == "new_trading_day_wait"


def test_in_memory_strength_is_not_cleared_without_restart_fallback():
    controller = DisplayOrderController()
    row = {
        "stock_code": "000001",
        "candidate_score": 80,
        "strength_5m": 123.4,
        "strength_snapshot_at": "2000-01-01T15:00:00",
    }
    result = controller.apply([row])[0]

    assert result["strength_5m"] == 123.4
