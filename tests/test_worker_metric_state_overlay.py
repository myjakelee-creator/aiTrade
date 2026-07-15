from __future__ import annotations

import threading
from types import SimpleNamespace

import realtime_v2.worker_metric_state_overlay_patch as overlay
import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle
import realtime_v2.worker_six_metric_output_guard as output_guard


class OverlayState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"metric_session_state_date": "20260715"}
        self.daily_values_by_code = {
            "000660": {
                "bid_ask_ratio": 1.56,
                "orderbook_source": "ka10004_rest_lowload",
                "orderbook_status": "ok",
                "orderbook_source_trading_date": "20260715",
                "strength_5m": 94.64,
                "strength_source": "ka10046_rest_lowload",
                "strength_status": "ok",
                "strength_source_trading_date": "20260715",
                "program_net": 3824,
                "program_net_source": "ka90004_tr_singleflight",
                "program_net_status": "ok",
                "program_source_trading_date": "20260715",
            }
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "trade_value_eok": 200.0,
                "prev_trade_value_eok": 100.0,
            }
        }

    def rows(self, limit: int = 300):
        # Reproduce a stale heavy snapshot: current price fields exist, but the
        # newly collected auxiliary metrics have not been copied into this row.
        return [
            {
                "stock_code": "000660",
                "trade_value_eok": 200.0,
                "prev_trade_value_eok": 100.0,
            }
        ][:limit]

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        return False


class OverlayBase:
    State = OverlayState
    DAILY_PERSIST_KEYS = ()


def _session():
    return SimpleNamespace(
        phase="before_market",
        trading_date="20260716",
        calendar_date="20260716",
        is_trading_day=True,
        windows={"premarket_start": "08:00"},
    )


def test_authoritative_state_survives_lifecycle_and_output_guard(monkeypatch):
    monkeypatch.setattr(overlay, "market_session_now", lambda now=None: _session())
    monkeypatch.setattr(lifecycle, "market_session_now", lambda now=None: _session())
    monkeypatch.setattr(output_guard, "market_session_now", lambda now=None: _session())
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260715")
    monkeypatch.setattr(lifecycle, "_load_cache", lambda now=None: lifecycle._empty_cache())
    monkeypatch.setattr(lifecycle, "atomic_write_json", lambda path, payload: None)
    monkeypatch.setattr(
        lifecycle,
        "_next_premarket_boundary",
        lambda now=None: __import__("datetime").datetime(2026, 7, 16, 8, 0, 0),
    )

    overlay.install(OverlayBase)
    lifecycle.install(OverlayBase)
    output_guard.install(OverlayBase)

    state = OverlayBase.State()
    row = state.rows(100)[0]

    assert row["amount_ratio"] == 2.0
    assert row["amount_ratio_source_trading_date"] == "20260715"
    assert row["bid_ask_ratio"] == 1.56
    assert row["orderbook_source_trading_date"] == "20260715"
    assert row["strength_5m"] == 94.64
    assert row["strength_source_trading_date"] == "20260715"
    assert row["program_net"] == 3824
    assert row["program_source_trading_date"] == "20260715"

    assert state.status["metric_state_overlay_installed"] is True
    assert state.status["metric_state_overlay_orderbook_count"] == 1
    assert state.status["metric_state_overlay_strength5_count"] == 1
    assert state.status["six_metric_output_orderbook_accepted_count"] == 1
    assert state.status["six_metric_output_strength5_accepted_count"] == 1
    assert state.status["six_metric_output_program_accepted_count"] == 1


def test_amount_ratio_is_not_relabelled_when_state_date_mismatches(monkeypatch):
    class MismatchState(OverlayState):
        def __init__(self):
            super().__init__()
            self.status["metric_session_state_date"] = "20260714"

    class MismatchBase:
        State = MismatchState
        DAILY_PERSIST_KEYS = ()

    monkeypatch.setattr(overlay, "market_session_now", lambda now=None: _session())
    monkeypatch.setattr(lifecycle, "_expected_date", lambda session, now: "20260715")

    overlay.install(MismatchBase)
    state = MismatchBase.State()
    row = state.rows(100)[0]

    assert "amount_ratio" not in row
    assert "amount_ratio_source_trading_date" not in row
    assert row.get("amount_ratio_status") in {
        "source_date_mismatch_hidden",
        "final_output_guard_hidden",
    }
    assert state.status["metric_state_overlay_amount_ratio_stamped_count"] == 0
