from __future__ import annotations

import time
from collections import deque
from threading import RLock
from types import SimpleNamespace

from realtime_v2.strength5m_gap_policy_patch import install as install_gap_policy
from realtime_v2.strength5m_pending_watchdog_patch import (
    install as install_pending_watchdog,
)
from realtime_v2.strength5m_scheduler import Strength5mScheduler
from realtime_v2.strength5m_stale_status_patch import install as install_stale_status


install_stale_status()
install_pending_watchdog()
install_gap_policy()


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
        self._orderbook_probe_pending = deque()
        self._orderbook_probe_inflight = None
        self._orderbook_probe_last_request_at = 0.0
        self._opt10055_probe_pending = deque()
        self._opt10055_probe_inflight = None
        self._opt10055_probe_last_request_at = 0.0
        self._close_metrics_queue = deque()
        self._close_metrics_last_request_at = 0.0
        self._strength_probe_last_error = None

    def _strength_probe_ready_state_locked(self):
        return True, "ready"

    def _strength_probe_error(
        self,
        code,
        message,
        requested_at=None,
        trading_date=None,
    ):
        self._strength_probe_last_error = message
        return {
            "stock_code": code,
            "message": message,
            "requested_at": requested_at,
            "trading_date": trading_date,
        }


WEEKEND = SimpleNamespace(
    phase="weekend",
    is_trading_day=False,
    accept_realtime=False,
    windows={"regular_start": "09:00"},
)


def test_legacy_close_metrics_timestamp_does_not_starve_strength_backfill():
    provider = DummyProvider()
    provider._close_metrics_last_request_at = time.monotonic()
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=10.0) is True
    assert scheduler.preopen_block_reason == "ready"
    assert scheduler.preopen_strength_gap_remaining_sec == 0.0
    assert scheduler.preopen_cross_tr_gap_remaining_sec == 0.0


def test_strength_requests_keep_the_full_preopen_gap():
    provider = DummyProvider()
    provider._strength_probe_last_request_at = time.monotonic() - 2.0
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=10.0) is False
    assert scheduler.preopen_block_reason == "strength_request_gap"
    assert 7.0 <= scheduler.preopen_strength_gap_remaining_sec <= 8.5


def test_other_real_tr_uses_only_the_short_cross_tr_safety_gap():
    provider = DummyProvider()
    provider._strength_probe_last_request_at = time.monotonic() - 30.0
    provider._orderbook_probe_last_request_at = time.monotonic() - 0.2
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=10.0) is False
    assert scheduler.preopen_block_reason == "cross_tr_safety_gap"
    assert 0.5 <= scheduler.preopen_cross_tr_gap_remaining_sec <= 1.25


def test_backfill_resumes_after_strength_and_cross_tr_gaps_expire():
    provider = DummyProvider()
    provider._strength_probe_last_request_at = time.monotonic() - 30.0
    provider._orderbook_probe_last_request_at = time.monotonic() - 3.0
    provider._opt10055_probe_last_request_at = time.monotonic() - 3.0
    provider._close_metrics_last_request_at = time.monotonic()
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=10.0) is True
    assert scheduler.preopen_block_reason == "ready"

    result = scheduler.stats()
    assert result["preopen_gap_policy"] == "strength_10s_cross_tr_normal_gap"
    assert result["preopen_close_metrics_last_age_sec"] is not None
