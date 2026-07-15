from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import board_metric_continuity_patch as continuity
from realtime_v2 import market_session


def _cached_entry(date_text: str = "20260713") -> dict:
    return {
        "stock_code": "005930",
        "bid_ask_ratio": 1.7,
        "bid_pct": 63,
        "ask_pct": 37,
        "orderbook_received_at": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}T19:59:50+09:00",
        "_metric_continuity_orderbook_date": date_text,
        "execution_strength": 145.0,
        "execution_strength_updated_at": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}T19:59:50+09:00",
        "_metric_continuity_execution_date": date_text,
        "strength_5m": 132.0,
        "strength_snapshot_at": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}T19:59:00+09:00",
        "_metric_continuity_strength5_date": date_text,
        "program_net": -12.5,
        "program_net_updated_at": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}T19:59:00+09:00",
        "program_net_status": "ok",
        "_metric_continuity_program_date": date_text,
        "large_trade_buy_count": 10,
        "large_trade_sell_count": 4,
        "large_trade_net_count": 6,
        "large_trade_buy_sum_eok": 8.0,
        "large_trade_sell_sum_eok": 3.0,
        "large_trade_net_sum_eok": 5.0,
        "large_trade_source": "collector_aggregate",
        "large_trade_updated_at": f"{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}T19:59:59+09:00",
        "_metric_continuity_large_trade_date": date_text,
    }


def _build_base(row: dict):
    class FakeState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.daily_values_by_code = {}
            self._rows = [deepcopy(row)]

        def rows(self, limit: int = 300):
            return deepcopy(self._rows[:limit])

        def persist_daily_state_if_needed(self, force: bool = False):
            return True

    base = SimpleNamespace(State=FakeState, DAILY_PERSIST_KEYS=())
    continuity.install(base)
    return base


def _write_cache(path: Path, entry: dict) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "basis_trading_date": "20260713",
                "values": {"005930": entry},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_closed_reconnect_restores_every_required_metric(tmp_path, monkeypatch):
    cache_path = tmp_path / "board_metric_continuity.json"
    _write_cache(cache_path, _cached_entry())
    monkeypatch.setattr(continuity, "CONTINUITY_PATH", cache_path)
    monkeypatch.setattr(
        continuity,
        "_session_info",
        lambda now=None: {
            "session": {},
            "phase": "closed",
            "started": False,
            "current_date": "20260713",
            "reference_date": "20260713",
            "hold_until": "2026-07-14T08:00:00",
        },
    )

    base = _build_base({"stock_code": "005930"})
    state = base.State()
    row = state.rows()[0]

    assert row["bid_ask_ratio"] == 1.7
    assert row["execution_strength"] == 145.0
    assert row["strength_5m"] == 132.0
    assert row["program_net"] == -12.5
    assert row["large_trade_net_count"] == 6
    assert row["large_trade_net_sum_eok"] == 5.0
    assert row["metric_continuity_basis"] == "last_session_hold"
    assert row["metric_continuity_valid_until"] == "2026-07-14T08:00:00"
    assert not row.get("metric_scoring_blocked_groups")


def test_weekend_and_delayed_open_before_market_keep_last_session_values(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "board_metric_continuity.json"
    _write_cache(cache_path, _cached_entry())
    monkeypatch.setattr(continuity, "CONTINUITY_PATH", cache_path)
    monkeypatch.setattr(
        continuity,
        "_session_info",
        lambda now=None: {
            "session": {},
            "phase": "before_market",
            "started": False,
            "current_date": "20260715",
            "reference_date": "20260713",
            "hold_until": "2026-07-15T09:00:00",
        },
    )

    base = _build_base({"stock_code": "005930"})
    row = base.State().rows()[0]

    assert row["bid_ask_ratio"] == 1.7
    assert row["execution_strength"] == 145.0
    assert row["strength_5m"] == 132.0
    assert row["program_net"] == -12.5
    assert row["large_trade_net_count"] == 6
    assert row["metric_continuity_reference_date"] == "20260713"
    assert row["metric_continuity_valid_until"] == "2026-07-15T09:00:00"


def test_actual_new_premarket_keeps_snapshots_visible_and_resets_cumulative_metrics(
    tmp_path, monkeypatch
):
    cache_path = tmp_path / "board_metric_continuity.json"
    _write_cache(cache_path, _cached_entry())
    monkeypatch.setattr(continuity, "CONTINUITY_PATH", cache_path)
    monkeypatch.setattr(
        continuity,
        "_session_info",
        lambda now=None: {
            "session": {},
            "phase": "premarket",
            "started": True,
            "current_date": "20260714",
            "reference_date": "20260714",
            "hold_until": "2026-07-15T08:00:00",
        },
    )

    base = _build_base({"stock_code": "005930"})
    row = base.State().rows()[0]

    assert row["bid_ask_ratio"] == 1.7
    assert row["execution_strength"] == 145.0
    assert row["strength_5m"] == 132.0
    assert set(row["metric_scoring_blocked_groups"]) >= {
        "orderbook",
        "execution",
        "strength5",
        "program",
        "large_trade",
    }
    assert row["program_net"] == 0.0
    assert row["program_net_status"] == "new_session_wait"
    assert row["large_trade_buy_count"] == 0
    assert row["large_trade_sell_count"] == 0
    assert row["large_trade_net_count"] == 0
    assert row["large_trade_status"] == "new_session_wait"


def test_calendar_skips_holiday_and_respects_delayed_premarket(tmp_path, monkeypatch):
    calendar_path = tmp_path / "stockboard_market_calendar.json"
    calendar_path.write_text(
        json.dumps(
            {
                "default_windows": {
                    "premarket_start": "08:00",
                    "opening_call_start": "08:30",
                    "regular_start": "09:00",
                    "closing_call_start": "15:20",
                    "regular_close": "15:30",
                    "aftermarket_start": "15:40",
                    "aftermarket_end": "20:00",
                },
                "holidays": ["20260714"],
                "special_days": {
                    "20260715": {
                        "open_delay_minutes": 60,
                        "reason": "delayed open test",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(market_session, "CONFIG_PATH", calendar_path)

    result = market_session.next_premarket_datetime(
        datetime(2026, 7, 13, 20, 1, 0)
    )

    assert result == datetime(2026, 7, 15, 9, 0, 0)


def test_continuity_source_contains_no_tr_or_qax_path():
    source = Path("realtime_v2/board_metric_continuity_patch.py").read_text(
        encoding="utf-8"
    )
    assert "dynamicCall" not in source
    assert "QAxWidget" not in source
    assert "kiwoom_data_provider" not in source
    assert "coordinator.execute" not in source
    assert "next_premarket_datetime" in source
    assert "last_completed_trading_date" in source
