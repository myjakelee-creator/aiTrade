from __future__ import annotations

from collections import deque
from threading import RLock
from types import SimpleNamespace

import realtime_v2.strength5m_pending_watchdog_patch as pending_patch
from realtime_v2.strength5m_pending_watchdog_patch import install as install_pending
from realtime_v2.strength5m_scheduler import Strength5mScheduler
from realtime_v2.strength5m_stale_status_patch import install as install_stale


install_stale()
install_pending()


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

    def _strength_probe_ready_state_locked(self):
        return True, "ready"

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


def test_ready_orphan_pending_is_nudged_then_released_without_requested_at(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(pending_patch.time, "monotonic", lambda: clock[0])

    provider = DummyProvider()
    provider._strength_probe_pending.append(
        {
            "stock_code": "000001",
            "trading_date": "20260710",
            "next_retry_at_monotonic": 999.0,
        }
    )
    provider._strength_probe_pending_codes.add("000001")
    scheduler = Strength5mScheduler(DummyBase, provider)

    assert scheduler._idle(WEEKEND, gap_override=1.25) is False
    assert provider._strength_probe_pending[0]["next_retry_at_monotonic"] == 100.0
    assert scheduler.preopen_pending_nudge_count == 1
    assert scheduler.preopen_pending_nudge_last_code == "000001"
    assert scheduler.preopen_block_reason == "strength_pending"

    clock[0] = 116.0
    assert scheduler._idle(WEEKEND, gap_override=1.25) is True
    assert len(provider._strength_probe_pending) == 0
    assert "000001" not in provider._strength_probe_pending_codes
    assert provider.errors[-1]["stock_code"] == "000001"
    assert scheduler.preopen_watchdog_release_count == 1
    assert scheduler.preopen_watchdog_last_code == "000001"
    assert scheduler.preopen_watchdog_last_kind == "pending_orphan"


def test_pending_watchdog_stats_expose_nudge_and_observation(monkeypatch):
    clock = [200.0]
    monkeypatch.setattr(pending_patch.time, "monotonic", lambda: clock[0])

    provider = DummyProvider()
    provider._strength_probe_pending.append(
        {
            "stock_code": "000002",
            "next_retry_at_monotonic": 900.0,
        }
    )
    provider._strength_probe_pending_codes.add("000002")
    scheduler = Strength5mScheduler(DummyBase, provider)
    monkeypatch.setattr(scheduler, "_session", lambda: WEEKEND)

    scheduler._idle(WEEKEND, gap_override=1.25)
    result = scheduler.stats()

    assert result["preopen_orphan_pending_timeout_sec"] >= 10.0
    assert result["preopen_orphan_pending_seen_count"] == 1
    assert result["preopen_orphan_pending_seen_sample"] == ["000002"]
    assert result["preopen_pending_nudge_count"] == 1
    assert result["preopen_pending_nudge_last_code"] == "000002"
