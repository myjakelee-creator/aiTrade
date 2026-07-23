from __future__ import annotations

import threading
from pathlib import Path

from realtime_v2 import closed_metric_rank_hold_patch as hold
from realtime_v2 import metric_fast_sse_patch as metric


class _State:
    def __init__(self, phase: str, basis: str = ""):
        self.lock = threading.RLock()
        self.status = {
            "event_count": 7,
            "market_trading_date": "20260723",
            "board_market_phase": phase,
            "board_display_basis": basis,
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "trade_value_eok": 100.0,
                "trade_value_trading_date": "20260723",
                "amount_ratio": 1.5,
            },
            "005930": {
                "stock_code": "005930",
                "trade_value_eok": 80.0,
                "trade_value_trading_date": "20260723",
                "amount_ratio": 0.8,
            },
        }


def _snapshot(state: _State):
    return metric.build_metric_snapshot(
        state,
        limit=300,
        now_text=lambda: "2026-07-23T20:10:00+09:00",
    )


def test_closed_metric_payload_omits_rank_but_keeps_metrics():
    state = _State("closed", "after_close_live_checkpoint")
    payload = hold.strip_closed_rank(_snapshot(state), state)

    assert payload["metric_rank_mode"] == "closed_full_snapshot_rank_hold"
    assert all("rank" not in row for row in payload["rows"])
    assert payload["rows"][0].get("trade_value_eok") is not None
    assert payload["rows"][0].get("amount_ratio") is not None


def test_before_market_checkpoint_also_holds_full_snapshot_rank():
    state = _State("before_market", "after_close_live_checkpoint")
    payload = hold.strip_closed_rank(_snapshot(state), state)
    assert all("rank" not in row for row in payload["rows"])


def test_active_metric_payload_keeps_current_day_rank():
    state = _State("regular", "live_session_passthrough")
    payload = hold.strip_closed_rank(_snapshot(state), state)
    by_code = {row["stock_code"]: row for row in payload["rows"]}

    assert payload["metric_rank_mode"] == "active_current_day_rank"
    assert by_code["000660"]["rank"] == 1
    assert by_code["005930"]["rank"] == 2


def test_patch_does_not_add_market_data_or_browser_calculation():
    source = Path(hold.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "requests.",
        "urlopen(",
        "WebSocket(",
        "threading.Thread",
        "javascript",
    ):
        assert forbidden not in source
