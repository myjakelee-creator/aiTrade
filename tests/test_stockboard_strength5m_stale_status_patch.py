from __future__ import annotations

from threading import RLock

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
