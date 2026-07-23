from __future__ import annotations

from realtime_v2.closed_metric_alias_restore_patch import normalize_metric_aliases


def test_orderbook_ui_aliases_restore_standard_last_good_fields():
    values = normalize_metric_aliases(
        {
            "ui_bid_ask_ratio": 2.66,
            "ui_bid_pct": 72.7,
            "ui_ask_pct": 27.3,
            "ui_bid_volume": 1000,
            "ui_ask_volume": 376,
            "ui_orderbook_observed_at": "2026-07-23T15:29:59+09:00",
            "ui_orderbook_source_trading_date": "20260723",
        }
    )

    assert values["bid_ask_ratio"] == 2.66
    assert values["bid_volume"] == 1000
    assert values["ask_volume"] == 376
    assert values["orderbook_received_at"] == "2026-07-23T15:29:59+09:00"
    assert values["_session_hold_orderbook_date"] == "20260723"


def test_strength_and_execution_remain_separate_alias_lanes():
    values = normalize_metric_aliases(
        {
            "ui_execution_strength": 111.0,
            "ui_execution_source_trading_date": "20260723",
            "execution_strength_source": "kiwoom_rest_ws_0B_fid228",
            "ui_strength_5m": 109.8,
            "ui_strength_source_trading_date": "20260723",
            "strength_source": "ka10046_rest_lowload",
        }
    )

    assert values["execution_strength"] == 111.0
    assert values["strength_5m"] == 109.8
    assert values["execution_strength"] != values["strength_5m"]
    assert values["_session_hold_execution_date"] == "20260723"
    assert values["_session_hold_strength5_date"] == "20260723"


def test_normalizer_does_not_invent_execution_source_or_network_work():
    values = normalize_metric_aliases({"ui_execution_strength": 100.0})
    assert values["execution_strength"] == 100.0
    assert "execution_strength_source" not in values
