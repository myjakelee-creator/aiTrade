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


def test_shared_cache_recomputes_after_event_change():
    state = FakeState()
    service = StockBoardSnapshotCacheService(state, heartbeat_sec=10)
    service.refresh(force=True)
    state.status["event_count"] += 1
    assert service.refresh(force=False) is True
    assert state.calls == 2
    assert service.status()["cache_version"] == 2


def test_shared_cache_slices_rest_payload_without_recompute():
    state = FakeState()
    service = StockBoardSnapshotCacheService(state)
    service.refresh(force=True)
    payload = service.get_payload(limit=1, refresh_if_changed=False)
    assert payload["row_count"] == 1
    assert len(payload["rows"]) == 1
    assert state.calls == 1
