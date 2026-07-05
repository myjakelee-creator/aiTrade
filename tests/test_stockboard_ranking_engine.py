from stockboard_ranking_engine import (
    NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS,
    NET_BUY_STRENGTH_TOTAL_POINTS,
    enrich_net_buy_strength_v02_fields,
    grade_text_for_percent,
)


def test_grade_policy_bands_are_fixed():
    assert grade_text_for_percent(59) == "F59"
    assert grade_text_for_percent(60) == "D60"
    assert grade_text_for_percent(69) == "D69"
    assert grade_text_for_percent(70) == "C70"
    assert grade_text_for_percent(79) == "C79"
    assert grade_text_for_percent(80) == "B80"
    assert grade_text_for_percent(89) == "B89"
    assert grade_text_for_percent(90) == "A90"


def test_net_buy_strength_v02_scores_and_pool():
    rows = [
        {
            "stock_code": "000001",
            "stock_name": "strong_sample",
            "rank": 1,
            "prev_rank": 80,
            "trade_value_eok": 100,
            "prev_trade_value_eok": 10,
            "bid_volume": 300000,
            "ask_volume": 700000,
            "realtime_strength": 200,
            "one_min_strength_growth_rate": 10,
            "program_net": 20,
        },
        {
            "stock_code": "000002",
            "stock_name": "middle_sample",
            "rank": 2,
            "prev_rank": 20,
            "trade_value_eok": 200,
            "prev_trade_value_eok": 100,
            "bid_volume": 500000,
            "ask_volume": 500000,
            "realtime_strength": 100,
            "one_min_strength_growth_rate": 5,
            "program_net": 5,
        },
        {
            "stock_code": "000003",
            "stock_name": "fallback_sample",
            "rank": 3,
            "prev_rank": 3,
            "trade_value_eok": 50,
            "prev_trade_value_status": "fallback",
            "bid_volume": 900000,
            "ask_volume": 100000,
            "realtime_strength": 50,
            "one_min_strength_growth_rate": 0,
            "program_net": -1,
        },
    ]

    result = enrich_net_buy_strength_v02_fields(rows, {"id": "NET_BUY_STRENGTH_V02"})
    by_code = {row["stock_code"]: row for row in result}

    assert by_code["000001"]["candidate_grade_text"].startswith("A")
    assert by_code["000001"]["is_candidate"] is True
    assert by_code["000001"]["funnel_rank"] == 1
    assert by_code["000001"]["score_possible_points"] == NET_BUY_STRENGTH_TOTAL_POINTS

    fallback_items = by_code["000003"]["score_breakdown"]["net_buy_strength"]["items"]
    amount_item = next(item for item in fallback_items if item["key"] == "trade_value_ratio")
    assert amount_item["points"] == 60
    assert amount_item["status"] == "fallback"

    assert by_code["000003"]["candidate_grade_text"].startswith("F")


def _result_for(row):
    return enrich_net_buy_strength_v02_fields([row], {"id": "NET_BUY_STRENGTH_V02"})[0]


def _one_min_item_for(result_row):
    items = result_row["score_breakdown"]["net_buy_strength"]["items"]
    return next(item for item in items if item["key"] == "one_min_strength")


def test_after_close_five_min_strength_is_display_only_and_denominator_is_600():
    result = _result_for(
        {
            "stock_code": "000001",
            "rank": 1,
            "prev_rank": 1,
            "trade_value_eok": 100,
            "prev_trade_value_eok": 100,
            "bid_volume": 100,
            "ask_volume": 100,
            "realtime_strength": 100,
            "strength_5m": 150,
            "program_net": 0,
        }
    )
    item = _one_min_item_for(result)

    assert item["points"] == 0
    assert item["possible_points"] == 0
    assert item["status"] == "display_only"
    assert item["value"] == 150
    assert item["source"] == "strength_5m_after_close"
    assert result["score_possible_points"] == NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS


def test_regular_missing_one_min_and_five_min_strength_gets_neutral_50_with_700_denominator():
    result = _result_for(
        {
            "stock_code": "000003",
            "rank": 1,
            "prev_rank": 1,
            "trade_value_eok": 100,
            "prev_trade_value_eok": 100,
            "bid_volume": 100,
            "ask_volume": 100,
            "realtime_strength": 100,
            "program_net": 0,
        }
    )
    item = _one_min_item_for(result)

    assert item["points"] == 50
    assert item["possible_points"] == 100
    assert item["status"] == "fallback"
    assert item["source"] == "one_min_strength_or_strength_5m_missing"
    assert result["score_possible_points"] == NET_BUY_STRENGTH_TOTAL_POINTS
