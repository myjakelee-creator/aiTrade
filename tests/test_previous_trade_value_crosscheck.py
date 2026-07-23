from __future__ import annotations

from stockboard_previous_trade_value import (
    PREVIOUS_VALUE_CONVERSION_VERSION,
    previous_trade_value_from_daily_row,
)


def test_explicit_million_value_is_used_when_close_volume_crosscheck_matches():
    row = {
        "date": "20260722",
        "trde_prica": "200000",
        "close_pric": "100000",
        "trde_qty": "2000000",
    }

    result = previous_trade_value_from_daily_row(row)

    assert result["prev_trade_value_eok"] == 2000.0
    assert result["prev_trade_value_source"] == (
        "ka10086_trade_value_million_crosschecked"
    )
    assert result["prev_trade_value_status"] == "ok"
    assert result["prev_trade_value_date"] == "20260722"
    assert result["prev_trade_value_conversion_version"] == (
        PREVIOUS_VALUE_CONVERSION_VERSION
    )


def test_severe_explicit_unit_mismatch_falls_back_to_close_times_volume():
    row = {
        "date": "20260722",
        "trde_prica": "20",
        "close_pric": "100000",
        "trde_qty": "2000000",
    }

    result = previous_trade_value_from_daily_row(row)

    assert result["prev_trade_value_eok"] == 2000.0
    assert result["prev_trade_value_source"] == (
        "prev_close_x_prev_volume_unit_mismatch_fallback"
    )
    assert result["prev_trade_value_status"] == "calculated_crosscheck_fallback"
    assert result["prev_trade_value_explicit_eok"] == 0.2
    assert result["prev_trade_value_crosscheck_ratio"] == 0.0001


def test_documented_trade_value_key_precedes_ambiguous_amount_alias():
    row = {
        "date": "20260722",
        "trde_prica": "200000",
        "amt_mn": "1",
        "close_pric": "100000",
        "trde_qty": "2000000",
    }

    result = previous_trade_value_from_daily_row(row)

    assert result["prev_trade_value_eok"] == 2000.0
    assert result["prev_trade_value_raw_million"] == 200000.0


def test_missing_explicit_value_uses_independent_close_volume_calculation():
    row = {
        "date": "20260722",
        "close_pric": "50000",
        "trde_qty": "1000000",
    }

    result = previous_trade_value_from_daily_row(row)

    assert result["prev_trade_value_eok"] == 500.0
    assert result["prev_trade_value_source"] == "prev_close_x_prev_volume"
    assert result["prev_trade_value_status"] == "calculated"
