from stockboard_ranking_engine import (
    NET_BUY_STRENGTH_TOTAL_POINTS,
    enrich_net_buy_strength_v02_fields,
    grade_text_for_percent,
)


def test_grade_policy_60_or_lower_is_f():
    assert grade_text_for_percent(60) == "F60"
    assert grade_text_for_percent(61) == "D61"
    assert grade_text_for_percent(71) == "C71"
    assert grade_text_for_percent(81) == "B81"
    assert grade_text_for_percent(91) == "A91"


def test_net_buy_strength_v02_scores_and_pool():
    rows = [
        {
            "stock_code": "000001",
            "stock_name": "강한종목",
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
            "stock_name": "중간종목",
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
            "stock_name": "약한종목",
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

    weak_breakdown = by_code["000003"]["score_breakdown"]["net_buy_strength"]["items"]
    amount_item = next(item for item in weak_breakdown if item["key"] == "trade_value_ratio")
    assert amount_item["points"] == 60
    assert amount_item["status"] == "fallback"

    assert by_code["000003"]["candidate_grade_text"].startswith("F")
