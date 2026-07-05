from stockboard_previous_trade_value import previous_trade_value_from_daily_row


def test_previous_trade_value_from_amount_million():
    row = {
        "dt": "20260703",
        "amt_mn": "12,345",
        "close_pric": "50,000",
        "trde_qty": "1,000",
    }

    result = previous_trade_value_from_daily_row(row)

    assert result["prev_trade_value_eok"] == 123.45
    assert result["prev_trade_value_source"] == "ka10086_amt_mn"
    assert result["prev_trade_value_status"] == "ok"
    assert result["prev_trade_value_date"] == "20260703"


def test_previous_trade_value_fallback_from_close_times_volume():
    row = {
        "dt": "20260703",
        "close_pric": "50,000",
        "trde_qty": "200,000",
    }

    result = previous_trade_value_from_daily_row(row)

    assert result["prev_trade_value_eok"] == 100.0
    assert result["prev_trade_value_source"] == "prev_close_x_prev_volume"
    assert result["prev_trade_value_status"] == "calculated"


def test_previous_trade_value_missing_is_not_zero():
    result = previous_trade_value_from_daily_row({"dt": "20260703"})

    assert result["prev_trade_value_eok"] is None
    assert result["prev_trade_value_status"] == "unavailable"
