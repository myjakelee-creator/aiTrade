from __future__ import annotations

import threading

from realtime_v2.board_platform.stockboard_cache import StockBoardSnapshotCacheService


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "event_count": 1,
            "trade_count": 1,
            "orderbook_count": 0,
            "program_net_last_at": "",
            "market_phase": "regular",
            "display_order_version": 0,
        }
        self.selected_candidate_model_id = "MODEL_A"
        self.calls = 0

    def snapshot(self, limit=300):
        self.calls += 1
        return {
            "ts": "2026-07-12T09:00:00.000",
            "status": dict(self.status),
            "row_count": 2,
            "rows": [{"stock_code": "005930"}, {"stock_code": "000660"}],
        }


def _make_due(service: StockBoardSnapshotCacheService) -> None:
    service.last_compute_mono -= max(1.0, service._next_delay() + 0.1)


def test_shared_cache_skips_unchanged_source():
    state = FakeState()
    service = StockBoardSnapshotCacheService(state, heartbeat_sec=10)
    assert service.refresh(force=True) is True
    assert state.calls == 1
    assert service.refresh(force=False) is False
    assert state.calls == 1
    version, body = service.get_bytes()
    assert version == 1
    assert b"005930" in body


def test_shared_cache_rate_limits_changed_source_until_scheduler_due():
    state = FakeState()
    service = StockBoardSnapshotCacheService(
        state,
        interval_sec=0.1,
        opening_interval_sec=0.1,
        slow_interval_sec=0.1,
    )
    service.refresh(force=True)
    state.status["event_count"] += 1

    assert service.refresh(force=False) is False
    assert state.calls == 1
    assert service.status()["compute_rate_limit_skip_count"] == 1

    _make_due(service)
    assert service.refresh(force=False) is True
    assert state.calls == 2
    assert service.status()["cache_version"] == 2


def test_rest_payload_read_requests_refresh_without_synchronous_recompute():
    state = FakeState()
    service = StockBoardSnapshotCacheService(state)
    service.refresh(force=True)
    state.status["event_count"] += 1

    payload = service.get_payload(limit=300, refresh_if_changed=True)

    assert payload["row_count"] == 2
    assert state.calls == 1
    status = service.status()
    assert status["refresh_request_count"] == 1
    assert status["scheduler_only"] is True


def test_shared_cache_coalesces_multiple_events_into_one_snapshot():
    state = FakeState()
    service = StockBoardSnapshotCacheService(
        state,
        interval_sec=0.1,
        opening_interval_sec=0.1,
        slow_interval_sec=0.1,
    )
    service.refresh(force=True)
    state.status["event_count"] += 5
    state.status["trade_count"] += 3
    state.status["orderbook_count"] += 2

    _make_due(service)
    assert service.refresh(force=False) is True

    status = service.status()
    assert state.calls == 2
    assert status["last_event_delta"] == 5
    assert status["coalesced_event_count"] == 4


def test_candidate_model_change_is_scheduled_as_forced_refresh():
    state = FakeState()
    service = StockBoardSnapshotCacheService(state)
    service.refresh(force=True)

    service.set_candidate_model("MODEL_B")

    assert state.selected_candidate_model_id == "MODEL_B"
    assert state.calls == 1
    status = service.status()
    assert status["force_refresh_request_count"] == 1
    assert service.force_refresh_requested is True


def test_shared_cache_slices_rest_payload_without_recompute():
    state = FakeState()
    service = StockBoardSnapshotCacheService(state)
    service.refresh(force=True)
    payload = service.get_payload(limit=1, refresh_if_changed=False)
    assert payload["row_count"] == 1
    assert len(payload["rows"]) == 1
    assert state.calls == 1
