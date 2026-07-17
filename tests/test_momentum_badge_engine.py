from __future__ import annotations

from copy import deepcopy

from realtime_v2.momentum_badge_engine import (
    MomentumBadgeEngine,
    load_momentum_badge_config,
)


def candle(minute, *, open_price, high, low, close, vwap):
    return {
        "minute_key": minute,
        "trading_date": "20260720",
        "phase": "regular",
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "vwap": vwap,
        "partial": False,
    }


def engine():
    return MomentumBadgeEngine(load_momentum_badge_config())


def test_config_separates_cross_trigger_from_directional_stay_condition():
    config = load_momentum_badge_config()
    breakout = next(rule for rule in config["rules"] if rule.rule_id == "open_breakout")

    assert config["schema_version"] == 2
    assert len(breakout.trigger_conditions) == 2
    assert len(breakout.stay_conditions) == 1


def test_simultaneous_open_and_vwap_breakout_produces_two_badges():
    subject = engine()
    subject.seed_last_candle(
        "000660",
        candle(10, open_price=99, high=101, low=98, close=99, vwap=100),
    )

    changed = subject.observe_completed_candle(
        "000660",
        candle(11, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )

    assert changed is True
    assert [item["badge"] for item in subject.badges("000660", 11)] == ["시돌", "중돌"]
    assert all(item["phase"] == "active" for item in subject.badges("000660", 11))


def test_breakout_stays_active_on_later_candle_above_both_reference_lines():
    subject = engine()
    subject.seed_last_candle(
        "000660",
        candle(10, open_price=99, high=101, low=98, close=99, vwap=100),
    )
    subject.observe_completed_candle(
        "000660",
        candle(11, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )

    changed = subject.observe_completed_candle(
        "000660",
        candle(12, open_price=103, high=105, low=102.8, close=104, vwap=102.5),
        day_open=100,
        trading_date="20260720",
    )

    assert changed is False
    assert [item["badge"] for item in subject.badges("000660", 12)] == ["시돌", "중돌"]
    assert all(item["phase"] == "active" for item in subject.badges("000660", 12))


def test_exit_uses_two_minute_hold_then_one_minute_fade_then_disappears():
    subject = engine()
    subject.seed_last_candle(
        "000660",
        candle(10, open_price=99, high=101, low=98, close=99, vwap=100),
    )
    subject.observe_completed_candle(
        "000660",
        candle(11, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )

    # Equality is neither an opposite trigger nor a valid stay condition.
    subject.observe_completed_candle(
        "000660",
        candle(12, open_price=100, high=101, low=99, close=100, vwap=100),
        day_open=100,
        trading_date="20260720",
    )

    assert all(item["phase"] == "grace" for item in subject.badges("000660", 12))
    assert all(item["phase"] == "grace" for item in subject.badges("000660", 13))
    assert all(item["phase"] == "fading" for item in subject.badges("000660", 14))
    assert subject.expire(15) == {"000660"}
    assert subject.badges("000660", 15) == []


def test_condition_recovery_during_grace_reactivates_existing_badge():
    subject = engine()
    subject.seed_last_candle(
        "000660",
        candle(10, open_price=99, high=101, low=98, close=99, vwap=100),
    )
    subject.observe_completed_candle(
        "000660",
        candle(11, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )
    subject.observe_completed_candle(
        "000660",
        candle(12, open_price=100, high=101, low=99, close=100, vwap=100),
        day_open=100,
        trading_date="20260720",
    )

    changed = subject.observe_completed_candle(
        "000660",
        candle(13, open_price=101, high=103, low=100.5, close=102, vwap=101),
        day_open=100,
        trading_date="20260720",
    )

    assert changed is True
    assert [item["phase"] for item in subject.badges("000660", 13)] == [
        "active",
        "active",
    ]


def test_last_matching_candle_stays_active_after_market_close_without_new_candle():
    subject = engine()
    subject.seed_last_candle(
        "000660",
        candle(929, open_price=99, high=101, low=98, close=99, vwap=100),
    )
    subject.observe_completed_candle(
        "000660",
        candle(930, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )

    # No later completed candle means the condition has not been observed to exit.
    assert subject.expire(1_440) == set()
    assert [item["phase"] for item in subject.badges("000660", 1_440)] == [
        "active",
        "active",
    ]


def test_alerts_include_lower_rows_inside_realtime_top100():
    subject = engine()
    subject.seed_last_candle(
        "123456",
        candle(10, open_price=99, high=101, low=98, close=99, vwap=100),
    )
    subject.observe_completed_candle(
        "123456",
        candle(11, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )

    rows = subject.alert_rows(
        {
            "123456": {
                "stock_name": "하단종목",
                "rank": 87,
            }
        },
        11,
    )

    assert len(rows) == 1
    assert rows[0]["rank"] == 87
    assert rows[0]["stock_name"] == "하단종목"
    assert [item["badge"] for item in rows[0]["badges"]] == ["시돌", "중돌"]


def test_state_round_trip_preserves_active_badges():
    config = load_momentum_badge_config()
    first = MomentumBadgeEngine(config)
    first.seed_last_candle(
        "000660",
        candle(10, open_price=99, high=101, low=98, close=99, vwap=100),
    )
    first.observe_completed_candle(
        "000660",
        candle(11, open_price=101, high=104, low=100, close=103, vwap=102),
        day_open=100,
        trading_date="20260720",
    )
    saved = deepcopy(first.serialize_code("000660", "20260720"))

    restored = MomentumBadgeEngine(config)
    restored.restore_code("000660", saved, "20260720")

    assert [item["badge"] for item in restored.badges("000660", 1000)] == ["시돌", "중돌"]
