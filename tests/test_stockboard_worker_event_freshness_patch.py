from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2.worker_event_freshness_patch import install


class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "event_count": 0,
            "trade_count": 0,
            "collector_status": None,
            "last_event_at": None,
        }

    def apply_event(self, event):
        with self.lock:
            self.status["event_count"] += 1
            self.status["last_event_at"] = event.get("ts")
            if event.get("type") == "collector_status":
                self.status["collector_status"] = dict(event)
            elif event.get("type") == "trade" and event.get("accepted", True):
                self.status["trade_count"] += 1


def make_base():
    ticks = iter(
        [
            "2026-07-13T12:00:10",
            "2026-07-13T12:00:11",
            "2026-07-13T12:00:12",
            "2026-07-13T12:00:13",
        ]
    )
    return SimpleNamespace(State=State, now_text=lambda: next(ticks))


def status(seq: int, ts: str):
    return {
        "type": "collector_status",
        "ts": ts,
        "collector_instance_id": "9368-test",
        "collector_status_seq": seq,
        "status": {"realdata_received_count": seq * 100},
    }


def test_older_collector_status_is_dropped_without_counter_regression():
    base = make_base()
    install(base)
    state = base.State()

    state.apply_event(status(2, "2026-07-13T12:00:02"))
    state.apply_event(status(1, "2026-07-13T12:00:01"))

    assert state.status["collector_status"]["collector_status_seq"] == 2
    assert state.status["collector_status_stale_drop_count"] == 1
    assert state.status["event_count"] == 2
    assert state.status["last_event_at"] == "2026-07-13T12:00:11"


def test_worker_liveness_uses_arrival_time_not_regressing_source_time():
    base = make_base()
    install(base)
    state = base.State()

    state.apply_event({"type": "trade", "ts": "2026-07-13T12:00:05"})
    state.apply_event({"type": "trade", "ts": "2026-07-13T11:59:59"})

    assert state.status["trade_count"] == 2
    assert state.status["last_event_at"] == "2026-07-13T12:00:11"
    assert state.status["last_event_source_at"] == "2026-07-13T11:59:59"
    assert state.status["last_trade_event_received_at"] == "2026-07-13T12:00:11"
