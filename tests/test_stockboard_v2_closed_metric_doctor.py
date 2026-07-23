from scripts.stockboard_v2_closed_metric_doctor import build_report


def test_build_report_separates_missing_previous_value_and_valid_metrics():
    payload = {
        "items": [
            {
                "stock_code": "000660",
                "stock_name": "SK하이닉스",
                "trade_value_eok": 134964,
                "prev_trade_value_eok": None,
                "prev_trade_value_source": "unavailable",
                "prev_trade_value_status": "missing_no_independent_crosscheck",
                "amount_ratio": None,
                "one_min_trade_value_eok": 0,
                "bid_ask_ratio": 2.66,
                "execution_strength": 100,
                "execution_strength_source": "kiwoom_rest_ws_0B_fid228_close_hold",
                "strength_5m": 99.9,
                "strength_source": "ka10046_rest_lowload",
            }
        ]
    }

    report = build_report(payload, 30)

    assert report["operator_only_no_production_path_change"] is True
    assert report["counts"]["rows"] == 1
    assert report["counts"]["prev_trade_value_valid"] == 0
    assert report["counts"]["amount_ratio_valid"] == 0
    assert report["counts"]["one_min_zero"] == 1
    assert report["counts"]["bid_ask_ratio_valid"] == 1
    assert report["counts"]["execution_strength_valid"] == 1
    assert report["counts"]["strength_5m_valid"] == 1
    assert report["prev_trade_value_status_counts"] == {
        "missing_no_independent_crosscheck": 1
    }
