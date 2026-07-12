from stockboard_theme_engine import ThemeBoardEngine
from stockboard_theme_master import ThemeDefinition, ThemeMaster, ThemeMember


def make_master():
    return ThemeMaster(themes=(
        ThemeDefinition("SEMICON", "반도체", (
            ThemeMember("005930", "primary"),
            ThemeMember("000660", "primary"),
        )),
        ThemeDefinition("POWER", "전력", (
            ThemeMember("010120", "primary"),
        )),
    ))


def rows(v1, v2, vp):
    return [
        {"stock_code": "005930", "stock_name": "삼성전자", "trade_value_eok": v1, "change_rate": 3.0, "execution_strength": 130, "strength_5m": 120, "program_net": 10, "large_trade_net_sum_eok": 5},
        {"stock_code": "000660", "stock_name": "SK하이닉스", "trade_value_eok": v2, "change_rate": 5.0, "execution_strength": 140, "strength_5m": 125, "program_net": 20, "large_trade_net_sum_eok": 8},
        {"stock_code": "010120", "stock_name": "LS ELECTRIC", "trade_value_eok": vp, "change_rate": 1.0, "execution_strength": 105, "strength_5m": 102, "program_net": -2, "large_trade_net_sum_eok": -1},
    ]


def test_warmup_then_one_and_five_minute_values():
    engine = ThemeBoardEngine(make_master())
    first = engine.update(rows(100, 200, 50), now_ts=0, trading_date="20260713")
    assert first["details"]["SEMICON"]["trade_value_1m_eok"] is None
    assert first["details"]["SEMICON"]["status"] == "WAIT_DATA"

    engine.update(rows(160, 300, 65), now_ts=60, trading_date="20260713")
    result = engine.update(rows(400, 700, 120), now_ts=300, trading_date="20260713")
    semi = result["details"]["SEMICON"]
    assert semi["trade_value_1m_eok"] == 640.0
    assert semi["trade_value_5m_eok"] == 800.0
    assert semi["breadth_pct"] == 100.0
    assert semi["leader_code"] in {"005930", "000660"}
    assert semi["coverage_status"] == "READY"


def test_trade_value_regression_resets_history():
    engine = ThemeBoardEngine(make_master())
    engine.update(rows(100, 200, 50), now_ts=0, trading_date="20260713")
    engine.update(rows(200, 300, 70), now_ts=60, trading_date="20260713")
    result = engine.update(rows(10, 20, 5), now_ts=120, trading_date="20260713")
    assert result["details"]["SEMICON"]["trade_value_1m_eok"] is None


def test_trading_date_change_resets_history():
    engine = ThemeBoardEngine(make_master())
    engine.update(rows(100, 200, 50), now_ts=0, trading_date="20260713")
    engine.update(rows(200, 300, 70), now_ts=60, trading_date="20260713")
    result = engine.update(rows(220, 330, 75), now_ts=120, trading_date="20260714")
    assert result["details"]["SEMICON"]["trade_value_1m_eok"] is None
