from __future__ import annotations

import pytest

from stockboard_theme_engine import ThemeEngine, ThemeMasterError, load_theme_master


def tiny_master():
    return {
        "schema_version": 1,
        "master_version": "TEST",
        "max_ranked_themes_per_stock": 3,
        "minimum_weight": 0.15,
        "themes": {
            "A": {"theme_name": "테마A", "enabled": True, "rank_eligible": True, "min_active_members": 1},
            "B": {"theme_name": "테마B", "enabled": True, "rank_eligible": True, "min_active_members": 1},
        },
        "stock_memberships": {
            "000001": [
                {"theme_id": "A", "weight": 0.7, "relation": "core"},
                {"theme_id": "B", "weight": 0.3, "relation": "related"},
            ]
        },
    }


def test_master_rejects_invalid_weight_sum():
    payload = tiny_master()
    payload["stock_memberships"]["000001"][1]["weight"] = 0.2
    with pytest.raises(ThemeMasterError, match="weight sum"):
        load_theme_master(payload)


def test_weighted_trade_value_is_not_duplicated():
    engine = ThemeEngine(load_theme_master(tiny_master()), display_theme_limit=10)
    rows = [{
        "stock_code": "000001",
        "stock_name": "테스트",
        "price": 1000,
        "change_rate": 3.0,
        "trade_value_eok": 100.0,
        "amount_ratio": 2.0,
        "execution_strength": 150,
        "strength_5m": 140,
        "program_net": 10,
        "large_trade_net_count": 2,
    }]
    payload = engine.compute(rows, now_mono=0.0)
    by_id = {item["theme_id"]: item for item in payload["themes"]}
    assert by_id["A"]["trade_value_text"] == "70억"
    assert by_id["B"]["trade_value_text"] == "30억"


def state_master():
    themes = {
        "HOT": {"theme_name": "강한테마", "enabled": True, "rank_eligible": True, "min_active_members": 3},
        "SLOW": {"theme_name": "약한테마", "enabled": True, "rank_eligible": True, "min_active_members": 3},
    }
    memberships = {}
    for index in range(3):
        memberships[f"{index + 1:06d}"] = [{"theme_id": "HOT", "weight": 1.0, "relation": "core"}]
        memberships[f"{index + 101:06d}"] = [{"theme_id": "SLOW", "weight": 1.0, "relation": "core"}]
    return load_theme_master({
        "schema_version": 1,
        "master_version": "STATE_TEST",
        "max_ranked_themes_per_stock": 3,
        "minimum_weight": 0.15,
        "themes": themes,
        "stock_memberships": memberships,
    })


def make_rows(hot_amount: float, slow_amount: float):
    rows = []
    for index in range(3):
        rows.append({
            "stock_code": f"{index + 1:06d}", "stock_name": f"H{index}", "price": 1000,
            "change_rate": 4.0 + index, "trade_value_eok": hot_amount / 3,
            "amount_ratio": 4.0, "execution_strength": 180, "strength_5m": 170,
            "program_net": 20 + index, "large_trade_net_count": 5 + index,
        })
        rows.append({
            "stock_code": f"{index + 101:06d}", "stock_name": f"S{index}", "price": 1000,
            "change_rate": -1.0, "trade_value_eok": slow_amount / 3,
            "amount_ratio": 0.8, "execution_strength": 90, "strength_5m": 90,
            "program_net": -5, "large_trade_net_count": -1,
        })
    return rows


def test_wait_data_then_hot_theme_becomes_ranked():
    engine = ThemeEngine(state_master(), display_theme_limit=10)
    first = engine.compute(make_rows(0, 0), now_mono=0.0)
    assert all(item["state_key"] == "WAIT_DATA" for item in first["themes"])
    engine.compute(make_rows(200, 10), now_mono=15.0)
    result = engine.compute(make_rows(500, 12), now_mono=31.0)
    hot = next(item for item in result["themes"] if item["theme_id"] == "HOT")
    assert hot["display_rank"] == 1
    assert hot["state_key"] in {"RISING", "SURGE"}
    assert hot["leaders"][0]["role_text"] in {"주도", "동반"}


def test_concentration_warning_is_server_output():
    master = load_theme_master({
        "schema_version": 1,
        "master_version": "CONCENTRATION",
        "max_ranked_themes_per_stock": 3,
        "minimum_weight": 0.15,
        "themes": {"A": {"theme_name": "집중테마", "enabled": True, "rank_eligible": True, "min_active_members": 3}},
        "stock_memberships": {
            "000001": [{"theme_id": "A", "weight": 1.0, "relation": "core"}],
            "000002": [{"theme_id": "A", "weight": 1.0, "relation": "core"}],
            "000003": [{"theme_id": "A", "weight": 1.0, "relation": "core"}],
        },
    })
    engine = ThemeEngine(master)
    rows = [
        {"stock_code": "000001", "stock_name": "대장", "price": 1, "change_rate": 5, "trade_value_eok": 90, "amount_ratio": 5, "execution_strength": 200, "strength_5m": 180, "program_net": 10, "large_trade_net_count": 5},
        {"stock_code": "000002", "stock_name": "둘", "price": 1, "change_rate": 1, "trade_value_eok": 5, "amount_ratio": 1, "execution_strength": 100, "strength_5m": 100, "program_net": 0, "large_trade_net_count": 0},
        {"stock_code": "000003", "stock_name": "셋", "price": 1, "change_rate": 1, "trade_value_eok": 5, "amount_ratio": 1, "execution_strength": 100, "strength_5m": 100, "program_net": 0, "large_trade_net_count": 0},
    ]
    payload = engine.compute(rows, now_mono=40.0)
    theme = payload["themes"][0]
    assert theme["concentration_warning"] is True
    assert theme["leader_concentration_text"] == "90%"
