from __future__ import annotations

from datetime import datetime, timedelta

from stockboard_ranking_engine import (
    FIVE_FACTOR_FLOW_V01,
    NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS,
    NET_BUY_STRENGTH_TOTAL_POINTS,
    enrich_candidate_model_fields,
    enrich_net_buy_strength_v02_fields,
    grade_text_for_percent,
    load_candidate_model_registry,
)


def _now() -> str:
    return datetime.now().isoformat()


def test_grade_policy_is_direct_from_score():
    assert grade_text_for_percent(59) == "F59"
    assert grade_text_for_percent(60) == "D60"
    assert grade_text_for_percent(70) == "C70"
    assert grade_text_for_percent(80) == "B80"
    assert grade_text_for_percent(90) == "A90"


def test_five_factor_model_ignores_orderbook_and_one_minute_strength():
    base = {
        "stock_code": "000001",
        "rank": 1,
        "prev_rank": 100,
        "trade_value_eok": 100,
        "prev_trade_value_eok": 10,
        "execution_strength": 200,
        "execution_strength_updated_at": _now(),
        "program_net": 20,
        "program_net_updated_at": _now(),
        "large_trade_buy_count": 10,
        "large_trade_sell_count": 1,
        "large_trade_buy_sum_eok": 20,
        "large_trade_sell_sum_eok": 1,
        "large_trade_net_sum_eok": 19,
    }
    changed = dict(base, bid_ask_ratio=0.1, strength_1m=999)
    result_a = enrich_candidate_model_fields([base], FIVE_FACTOR_FLOW_V01)[0]
    result_b = enrich_candidate_model_fields([changed], FIVE_FACTOR_FLOW_V01)[0]
    assert result_a["candidate_score"] == result_b["candidate_score"]
    assert result_a["candidate_grade_text"] == result_b["candidate_grade_text"]


def test_five_factor_strong_row_gets_a_and_exposes_components():
    row = {
        "stock_code": "000001",
        "rank": 1,
        "prev_rank": 100,
        "trade_value_eok": 100,
        "prev_trade_value_eok": 10,
        "execution_strength": 200,
        "execution_strength_updated_at": _now(),
        "program_net": 20,
        "program_net_updated_at": _now(),
        "large_trade_buy_count": 10,
        "large_trade_sell_count": 1,
        "large_trade_buy_sum_eok": 20,
        "large_trade_sell_sum_eok": 1,
        "large_trade_net_sum_eok": 19,
    }
    result = enrich_candidate_model_fields([row], FIVE_FACTOR_FLOW_V01)[0]
    assert result["candidate_score"] >= 90
    assert result["candidate_grade_text"].startswith("A")
    assert result["rank_rise_score"] == 100
    assert result["amount_ratio_score"] == 100
    assert result["instant_strength_score"] == 100
    assert result["program_score"] == 100
    assert result["large_trade_score"] > 90
    assert result["combination_score"] == 25
    assert result["pool_stage"] == "top20"


def test_stale_realtime_and_program_values_do_not_score():
    old = (datetime.now() - timedelta(minutes=3)).isoformat()
    row = {
        "stock_code": "000001",
        "rank": 1,
        "prev_rank": 100,
        "trade_value_eok": 100,
        "prev_trade_value_eok": 10,
        "execution_strength": 200,
        "execution_strength_updated_at": old,
        "program_net": 20,
        "program_net_updated_at": old,
        "large_trade_buy_count": 0,
        "large_trade_sell_count": 0,
        "large_trade_buy_sum_eok": 0,
        "large_trade_sell_sum_eok": 0,
        "large_trade_net_sum_eok": 0,
    }
    result = enrich_candidate_model_fields([row], FIVE_FACTOR_FLOW_V01)[0]
    assert result["instant_strength_score"] == 0
    assert result["program_score"] == 0
    assert result["score_status"] == "stale"


def test_net_buy_v02_uses_five_minute_strength():
    row = {
        "stock_code": "000001",
        "rank": 1,
        "prev_rank": 10,
        "trade_value_eok": 100,
        "prev_trade_value_eok": 50,
        "bid_volume": 100,
        "ask_volume": 100,
        "execution_strength": 100,
        "strength_1m": 999,
        "strength_5m": 150,
        "program_net": 10,
    }
    result = enrich_net_buy_strength_v02_fields([row])[0]
    items = result["score_breakdown"]["net_buy_strength"]["items"]
    item = next(item for item in items if item["key"] == "five_min_strength")
    assert item["source"] == "strength_5m"
    assert item["value"] == 150
    assert result["score_possible_points"] == NET_BUY_STRENGTH_TOTAL_POINTS
    assert NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS == NET_BUY_STRENGTH_TOTAL_POINTS


def test_registry_defaults_to_new_model_and_dropdown_source_contains_it():
    registry = load_candidate_model_registry()
    assert registry["default_model_id"] == FIVE_FACTOR_FLOW_V01
    assert any(model.get("id") == FIVE_FACTOR_FLOW_V01 for model in registry["models"])
