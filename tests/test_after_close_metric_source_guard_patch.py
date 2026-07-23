from realtime_v2.after_close_metric_source_guard_patch import sanitize_checkpoint_quote


def test_untrusted_execution_value_is_removed_and_strength_5m_kept():
    row = sanitize_checkpoint_quote({
        "stock_code": "005930",
        "execution_strength": 123.0,
        "ui_execution_strength": 123.0,
        "strength_5m": 123.0,
        "strength_source": "ka10046_rest_lowload",
    })
    assert "execution_strength" not in row
    assert "ui_execution_strength" not in row
    assert row["strength_5m"] == 123.0


def test_trusted_execution_value_is_preserved_independently():
    row = sanitize_checkpoint_quote({
        "stock_code": "000660",
        "execution_strength": 100.0,
        "execution_strength_source": "kiwoom_rest_ws_0B_fid228_close_hold",
        "strength_5m": 96.85,
        "strength_source": "ka10046_rest_lowload",
    })
    assert row["execution_strength"] == 100.0
    assert row["strength_5m"] == 96.85


def test_price_and_trade_value_are_unchanged():
    row = sanitize_checkpoint_quote({
        "stock_code": "000660",
        "price": 1946000,
        "change_rate": 6.34,
        "trade_value_eok": 134964.0,
        "execution_strength": 100.0,
        "strength_5m": 100.0,
    })
    assert row["price"] == 1946000
    assert row["change_rate"] == 6.34
    assert row["trade_value_eok"] == 134964.0
    assert "execution_strength" not in row
    assert row["strength_5m"] == 100.0
