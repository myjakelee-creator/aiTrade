from __future__ import annotations

from datetime import datetime

from stockboard_ranking_engine import (
    enrich_candidate_model_fields,
    load_candidate_model_registry,
    validate_candidate_model_config,
)


def _row(
    index: int,
    *,
    change_rate: float = 3.0,
    amount_ratio: float = 2.0,
    instant: float = 130.0,
    five: float | None = 120.0,
    program: float | None = 10.0,
    large_net: float | None = 5.0,
) -> dict:
    trade_value = 300.0 / max(index, 1)
    now = datetime.now().isoformat()
    return {
        "stock_code": f"{index:06d}",
        "rank": index,
        "prev_rank": 100 + index,
        "trade_value_eok": trade_value,
        "prev_trade_value_eok": trade_value / amount_ratio,
        "price": 105,
        "change_rate": change_rate,
        "ohlc": {"open": 100, "high": 108, "low": 98, "close": 105},
        "execution_strength": instant,
        "execution_strength_updated_at": now,
        "strength_5m": five,
        "program_net": program,
        "program_net_updated_at": now,
        "large_trade_buy_count": 10 if large_net is not None else None,
        "large_trade_sell_count": 2 if large_net is not None else None,
        "large_trade_buy_sum_eok": (large_net + 1) if large_net is not None else None,
        "large_trade_sell_sum_eok": 1 if large_net is not None else None,
        "large_trade_net_sum_eok": large_net,
    }


def test_registry_contains_only_eight_valid_nonlegacy_models():
    registry = load_candidate_model_registry(include_configs=True)

    assert len(registry["models"]) == 8
    assert registry["invalid_models"] == []
    assert all("TVRANK" not in str(model.get("id")) for model in registry["models"])
    assert set(registry["configs"]) == {str(model["id"]) for model in registry["models"]}


def test_all_registered_configs_use_only_validated_features():
    registry = load_candidate_model_registry(include_configs=True)
    forbidden = ("bid_ask", "sell_wall", "strength_1m", "one_min", "vwap")

    for model_id, config in registry["configs"].items():
        assert validate_candidate_model_config(config) == [], model_id
        text = str(config).lower()
        assert not any(token in text for token in forbidden), model_id


def test_every_model_runs_full_three_stage_funnel():
    registry = load_candidate_model_registry()
    rows = [_row(index, change_rate=10 - index / 20) for index in range(1, 61)]

    for model in registry["models"]:
        output = enrich_candidate_model_fields(rows, str(model["id"]))

        assert len(output) == 60
        assert [row["model_rank"] for row in output] == list(range(1, 61))
        assert sum(row["pool_stage"] == "top20" for row in output) == 20
        assert sum(bool(row["is_candidate"]) for row in output) <= 5
        assert all(row["model_validation_status"] == "READY" for row in output)
        assert all("entry_rank" in row for row in output)
        assert all("desired_top20" in row for row in output)


def test_board_relative_model_calculates_real_relative_change():
    rows = [_row(index, change_rate=-3 + index / 10) for index in range(1, 61)]
    output = enrich_candidate_model_fields(rows, "RELATIVE_STRENGTH_OPENING_V01")
    item = next(
        item
        for item in output[0]["score_breakdown"]["candidate_model"]["items"]
        if item["key"] == "relative_change"
    )

    assert item["source"] == "change_rate-board_median"
    assert item["status"] == "ok"
    assert item["value"] is not None


def test_missing_required_data_caps_score_and_blocks_funnel_promotion():
    row = _row(1, five=None, program=None, large_net=None)
    result = enrich_candidate_model_fields([row], "NET_BUY_STRENGTH_V02")[0]

    assert result["candidate_status"] == "WAIT_DATA"
    assert result["candidate_score"] <= 59
    assert result["desired_top20"] is False


def test_program_model_blocks_high_grade_when_program_is_negative():
    result = enrich_candidate_model_fields(
        [_row(1, program=-5.0)],
        "PROGRAM_FLOW_V01",
    )[0]

    assert result["candidate_score"] <= 59
    assert "프로양수" in result["grade_guard_failures"]


def test_validation_rejects_orderbook_and_unknown_features():
    invalid = {
        "id": "INVALID",
        "score_structure": {
            group: [{"key": "sell_wall_absorption", "weight": 100}]
            for group in ("final_score", "entry_score", "confirmation_score", "focus_score")
        },
        "funnel": {"top50": {"take": 50}, "top20": {"take": 20}, "top5": {"take": 5}},
    }

    errors = validate_candidate_model_config(invalid)
    assert any(error.startswith("unsupported_feature:") for error in errors)
    assert any(error.startswith("forbidden_feature:") for error in errors)
