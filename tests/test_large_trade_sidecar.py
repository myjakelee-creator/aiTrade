from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from realtime_v2.large_trade_collector32 import (
    large_trade_delta,
    tracking_limit_for_time,
)
from realtime_v2.worker_large_trade_sidecar_patch import install

ROOT = Path(__file__).resolve().parents[1]
PRICE_COLLECTOR = ROOT / "realtime_v2" / "collector32_large_bidask.py"
SIDECAR = ROOT / "realtime_v2" / "large_trade_collector32.py"
MANAGER = ROOT / "scripts" / "stockboard_large_trade_sidecar.ps1"


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"event_count": 0}
        self.quotes = {}
        self.daily_values_by_code = {}
        self.daily_dirty = False

    def _quote(self, code):
        return self.quotes.setdefault(
            code,
            {
                "stock_code": code,
                "large_trade_buy_count": 2,
                "large_trade_sell_count": 1,
                "large_trade_net_count": 1,
                "large_trade_buy_sum_eok": 3.0,
                "large_trade_sell_sum_eok": 1.0,
                "large_trade_net_sum_eok": 2.0,
            },
        )

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def apply_event(self, event):
        self.status["event_count"] += 1
        self.status["last_event_at"] = event.get("ts")


class FakeBase:
    State = FakeState
    DAILY_PERSIST_KEYS = ()

    @staticmethod
    def merged_event_values(event):
        return dict(event.get("values") or {})


def test_adaptive_tracking_limit_protects_market_open():
    assert tracking_limit_for_time(
        datetime(2026, 7, 15, 9, 0), opening_limit=20, normal_limit=100
    ) == 20
    assert tracking_limit_for_time(
        datetime(2026, 7, 15, 9, 9), opening_limit=20, normal_limit=100
    ) == 20
    assert tracking_limit_for_time(
        datetime(2026, 7, 15, 9, 10), opening_limit=20, normal_limit=100
    ) == 100
    assert tracking_limit_for_time(
        datetime(2026, 7, 15, 12, 53), opening_limit=20, normal_limit=100
    ) == 100


def test_only_50m_or_larger_signed_ticks_emit_delta():
    assert large_trade_delta("100000", "+499") is None

    buy = large_trade_delta("100000", "+500")
    assert buy == {
        "large_trade_buy_count_delta": 1,
        "large_trade_sell_count_delta": 0,
        "large_trade_buy_sum_eok_delta": 0.5,
        "large_trade_sell_sum_eok_delta": 0.0,
        "large_trade_threshold_krw": 50_000_000,
        "large_trade_price": 100000,
        "large_trade_qty": 500,
    }

    sell = large_trade_delta("-200000", "-250")
    assert sell["large_trade_buy_count_delta"] == 0
    assert sell["large_trade_sell_count_delta"] == 1
    assert sell["large_trade_sell_sum_eok_delta"] == 0.5


def test_worker_adds_sparse_sidecar_deltas_without_touching_price():
    install(FakeBase)
    state = FakeState()
    quote = state._quote("000001")
    quote.update({"price": 123456, "change_rate": 7.89})

    state.apply_event(
        {
            "type": "large_trade_delta",
            "ts": "2026-07-15T12:53:00+09:00",
            "stock_code": "000001",
            "values": {
                "large_trade_buy_count_delta": 3,
                "large_trade_sell_count_delta": 1,
                "large_trade_buy_sum_eok_delta": 2.5,
                "large_trade_sell_sum_eok_delta": 0.75,
                "large_trade_source": "fid15_sidecar",
                "large_trade_updated_at": "2026-07-15T12:53:00+09:00",
                "trading_date": "20260715",
            },
        }
    )

    row = state.quotes["000001"]
    assert row["large_trade_buy_count"] == 5
    assert row["large_trade_sell_count"] == 2
    assert row["large_trade_net_count"] == 3
    assert row["large_trade_buy_sum_eok"] == 5.5
    assert row["large_trade_sell_sum_eok"] == 1.75
    assert row["large_trade_net_sum_eok"] == 3.75
    assert row["large_trade_source"] == "fid15_sidecar"
    assert row["large_trade_available"] is True
    assert row["price"] == 123456
    assert row["change_rate"] == 7.89
    assert state.daily_dirty is True
    assert state.status["large_trade_sidecar_event_count"] == 1


def test_price_collector_stays_fid15_free_and_sidecar_is_separate():
    price_source = PRICE_COLLECTOR.read_text(encoding="utf-8")
    sidecar_source = SIDECAR.read_text(encoding="utf-8")
    manager_source = MANAGER.read_text(encoding="utf-8")

    assert '_REALTIME_FIDS = "10;12;20;14"' in price_source
    assert "large_trade_enabled=False" in price_source
    assert "large_trade_collector32.py" not in price_source

    assert '_REALTIME_FIDS = "10;15;20"' in sidecar_source
    assert '"type": "large_trade_delta"' in sidecar_source
    assert "sender.publish_direct" in sidecar_source
    assert "publish_trade" not in sidecar_source
    assert "large_trade_collector32.py" in manager_source
