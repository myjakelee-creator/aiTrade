from __future__ import annotations

import time
from collections import deque
from threading import RLock
from types import SimpleNamespace

from realtime_v2.strength5m_scheduler import Strength5mScheduler
from realtime_v2.strength5m_stale_status_patch import install


install()


class DummyBase:
    @staticmethod
    def normalize_code(value):
        digits = "".join(ch for ch in str(value or "") if ch.isdigit())
        return digits[:6] if len(digits) >= 6 else ""


class DummyProvider:
    def __init__(self):
        self._lock = RLock()
        self._strength_probe_pending = deque()
        self._strength_probe_pending_codes = set()
        self._strength_probe_inflight = None
        self._strength_probe_timeout_sec = 10.0
        self._strength_probe_last_request_at = 0.0
        self._strength_probe_last_error = None
        self._orderbook_probe_pending = deque()
        self._orderbook_probe_inflight = None
        self._orderbook_probe_last_request_at = 0.0
        self._opt10055_probe_pending = deque()
        self._opt10055_probe_inflight = None
        self._opt10055_probe_last_request_at = 0.0
        self._close_metrics_queue = deque()
        self._close_metrics_last_request_at = 0.0
        self.errors = []

    def _strength_probe_error(
        self,
        code,
        message,
        requested_at=None,
        trading_date=None,
    ):
        self.errors.append(
            {
                "stock_code": code,
                "message": message,
                "requested_at": requested_at,
                "trading_date": trading_date,
            }
        )
        self._strength_probe_last_error = message
        return self.errors[-1]


WEEKEND = SimpleNamespace(
    phase="weekend",
    is_trading_day=False,
    accept_realtime=False,
    windows={"regular_start": "09:00"},
)


def test_stale_requested_status_is_released_for_preopen_retry():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {
        "stock_code": "000001",
        "lane": "top300",
        "row": {"strength_5m": None, "strength_status": "requested"},
    }

    due, score = scheduler._preopen_candidate_due(
        item,
        final_window=False,
        now_mono=100.0,
    )

    assert due is True
    assert score == float("inf")
    assert "000001" in scheduler.preopen_stale_status_released


def test_current_scheduler_request_still_respects_retry_backoff():
    scheduler = Strength5mScheduler(DummyBase, DummyProvider())
    item = {
        "stock_code": "000001",
        "lane": "top300",
        "row": {"strength_5m": None, "strength_status": "requested"},
    }
    scheduler.preopen_requested_at["000001"] = 90.0
    scheduler.preopen_retry_at["000001"] = 400.0

    due, remaining = scheduler._preopen_candidate_due(
        item,
        final_window=False,
        now_mono=100.0,
    )

    assert due is False
    assert remaining == 300.0


def test_preopen_idle_ignores_legacy_close_metrics_backlog():
    provider = DummyProvider()
    provider._close_metrics_queue.extend(["000001", "000002", "000003"])
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=1.25) is True
    assert scheduler.preopen_block_reason == "ready"
    assert scheduler.preopen_ignored_close_metrics_queue_count == 3


def test_preopen_idle_still_blocks_real_orderbook_pending_work():
    provider = DummyProvider()
    provider._orderbook_probe_pending.append({"stock_code": "000001"})
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=1.25) is False
    assert scheduler.preopen_block_reason == "orderbook_pending"


def test_preopen_watchdog_releases_stuck_strength_inflight_and_moves_on():
    provider = DummyProvider()
    provider._strength_probe_inflight = {
        "stock_code": "000001",
        "requested_at": "2026-07-11T09:00:00",
        "trading_date": "20260710",
        "started_at_monotonic": time.monotonic() - 30.0,
    }
    scheduler = Strength5mScheduler(DummyBase, provider)
    scheduler.preopen_attempts["000001"] = 1
    scheduler.preopen_requested_at["000001"] = time.monotonic() - 30.0

    assert scheduler._idle(WEEKEND, gap_override=1.25) is True
    assert provider._strength_probe_inflight is None
    assert provider.errors[-1]["stock_code"] == "000001"
    assert scheduler.preopen_watchdog_release_count == 1
    assert scheduler.preopen_watchdog_last_code == "000001"
    assert scheduler.preopen_watchdog_last_kind == "inflight"
    assert scheduler.preopen_retry_at["000001"] > time.monotonic()


def test_preopen_status_exposes_provider_blocking_diagnostics(monkeypatch):
    provider = DummyProvider()
    provider._close_metrics_queue.extend(["000001", "000002"])
    scheduler = Strength5mScheduler(DummyBase, provider)
    monkeypatch.setattr(scheduler, "_session", lambda: WEEKEND)

    result = scheduler.stats()

    assert result["provider_strength_pending_count"] == 0
    assert result["provider_strength_inflight_code"] is None
    assert result["provider_close_metrics_queue_count"] == 2
    assert "preopen_block_reason" in result
