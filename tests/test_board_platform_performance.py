from __future__ import annotations

import threading

from realtime_v2.board_platform.performance import BoardPerformanceService


class FakeService:
    def __init__(self, payload):
        self.payload = payload

    def status(self):
        return dict(self.payload)


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "market_phase": "regular",
            "last_event_at": "2999-01-01T00:00:00",
            "event_log_queue_size": 0,
            "event_log_dropped_count": 0,
            "stream_clients": 1,
            "tcp_clients": 1,
            "collector_status": {
                "sender_stats": {
                    "sent_per_sec": 120,
                    "pending_total_count": 0,
                    "dropped_count": 0,
                }
            },
        }


class FakeServer:
    def __init__(self):
        self.state = FakeState()
        self.stockboard_snapshot_cache = FakeService(
            {
                "state": "READY",
                "clients": 1,
                "cache_version": 3,
                "cache_age_ms": 100,
                "compute_ms": 20,
                "serialize_ms": 3,
                "payload_bytes": 100_000,
            }
        )
        self.theme_cache_service = FakeService(
            {
                "state": "READY",
                "theme_clients": 1,
                "cache_version": 2,
                "cache_age_ms": 500,
                "compute_ms": 3,
                "copy_ms": 0.4,
                "payload_bytes": 12_000,
            }
        )


def test_performance_payload_contains_common_and_board_metrics():
    service = BoardPerformanceService(FakeServer())
    payload = service.get_payload("stockboard")
    keys = {item["key"] for item in payload["display_metrics"]}
    assert {"bottleneck", "freshness", "recv", "compute", "serialize"} <= keys
    assert payload["boards"]["stockboard"]["cache_version"] == 3
