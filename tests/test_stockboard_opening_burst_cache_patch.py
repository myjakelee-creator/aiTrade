from __future__ import annotations

import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.worker_opening_burst_cache_patch import install


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "realtime_v2" / "worker_opening_burst_cache_patch.py"


def make_fake_base():
    class FakeState:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {
                "event_count": 1,
                "trade_count": 1,
                "orderbook_count": 0,
                "universe_count": 2,
            }
            self.quotes = {
                "000001": {
                    "stock_code": "000001",
                    "price": 100,
                    "trade_price": 100,
                    "change_rate": 1.0,
                    "received_at": "2026-07-13T09:00:00+09:00",
                    "row_source": "realtime",
                    "seed_rank": 1,
                },
                "000002": {
                    "stock_code": "000002",
                    "price": 200,
                    "trade_price": 200,
                    "change_rate": -1.0,
                    "received_at": "2026-07-13T09:00:00+09:00",
                    "row_source": "realtime",
                    "seed_rank": 2,
                },
            }
            self.selected_candidate_model_id = "model-a"
            self.heavy_builds = 0
            self.build_started = threading.Event()
            self.allow_build = threading.Event()
            self.allow_build.set()

        def logger_stats(self):
            return {"event_log_queue_size": 0}

        def persist_daily_state_if_needed(self):
            return None

        def snapshot(self, limit=300):
            with self.lock:
                self.heavy_builds += 1
                build_number = self.heavy_builds
            self.build_started.set()
            assert self.allow_build.wait(timeout=2.0)
            rows = []
            with self.lock:
                for code in sorted(self.quotes):
                    quote = deepcopy(self.quotes[code])
                    quote["candidate_score"] = build_number
                    quote["rank"] = len(rows) + 1
                    rows.append(quote)
                status = deepcopy(self.status)
            return {
                "schema_version": 1,
                "source": "fake",
                "ts": "heavy-build",
                "status": status,
                "row_count": len(rows[:limit]),
                "rows": rows[:limit],
            }

    writes = []
    fake_base = SimpleNamespace(
        State=FakeState,
        write_status_loop=lambda *_args, **_kwargs: None,
        atomic_write_json=lambda path, payload: writes.append((path, payload)),
    )
    return fake_base, writes


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def stop_background(state):
    state._opening_burst_cache_stop_event.set()
    state._opening_burst_cache_wakeup.set()


def test_background_cache_reuses_heavy_result_and_overlays_live_price(monkeypatch):
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", "5000")
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", "10000")
    monkeypatch.setenv("STOCKBOARD_STATUS_WRITE_INTERVAL_SEC", "5")

    fake_base, _writes = make_fake_base()
    install(fake_base)
    state = fake_base.State()
    assert state._opening_burst_cache_ready_event.wait(timeout=2.0)

    first = state.snapshot(limit=1)
    heavy_builds = state.heavy_builds
    assert first["rows"][0]["candidate_score"] == heavy_builds

    with state.lock:
        state.quotes["000001"]["price"] = 101
        state.quotes["000001"]["trade_price"] = 101
        state.quotes["000001"]["change_rate"] = 1.1
        state.status["event_count"] += 1
        state.status["trade_count"] += 1

    started = time.perf_counter()
    second = state.snapshot(limit=1)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert state.heavy_builds == heavy_builds
    assert second["rows"][0]["price"] == 101
    assert second["rows"][0]["change_rate"] == 1.1
    assert second["rows"][0]["candidate_score"] == heavy_builds
    assert second["status"]["opening_burst_background_enabled"] is True
    assert second["status"]["opening_burst_background_thread_alive"] is True
    assert second["status"]["opening_burst_cache_hit"] is True
    stop_background(state)


def test_http_snapshot_never_waits_for_background_heavy_build(monkeypatch):
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", "200")
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", "10000")

    fake_base, _writes = make_fake_base()
    install(fake_base)
    state = fake_base.State()
    assert state._opening_burst_cache_ready_event.wait(timeout=2.0)
    initial_builds = state.heavy_builds

    state.build_started.clear()
    state.allow_build.clear()
    time.sleep(0.22)
    with state.lock:
        state.quotes["000001"]["price"] = 101
        state.quotes["000001"]["trade_price"] = 101
        state.status["event_count"] += 1
        state.status["trade_count"] += 1

    state.snapshot(limit=1)
    assert state.build_started.wait(timeout=1.0)
    assert wait_until(lambda: state._opening_burst_cache_build_inflight)

    with state.lock:
        state.quotes["000001"]["price"] = 102
        state.quotes["000001"]["trade_price"] = 102

    started = time.perf_counter()
    during_build = state.snapshot(limit=1)
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert during_build["rows"][0]["price"] == 102
    assert during_build["status"]["opening_burst_background_build_inflight"] is True
    assert state.heavy_builds == initial_builds + 1

    state.allow_build.set()
    assert wait_until(
        lambda: state._opening_burst_cache_background_build_count >= 2,
        timeout=2.0,
    )
    stop_background(state)


def test_candidate_model_change_requests_immediate_background_rebuild(monkeypatch):
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", "5000")
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", "10000")

    fake_base, _writes = make_fake_base()
    install(fake_base)
    state = fake_base.State()
    assert state._opening_burst_cache_ready_event.wait(timeout=2.0)
    initial_builds = state.heavy_builds

    state.build_started.clear()
    state.selected_candidate_model_id = "model-b"
    state.snapshot(limit=2)

    assert state.build_started.wait(timeout=1.0)
    assert wait_until(lambda: state.heavy_builds > initial_builds)
    stop_background(state)


def test_patch_exposes_background_tuning_and_single_pending_job_policy():
    source = SOURCE_PATH.read_text(encoding="utf-8")
    assert "STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS" in source
    assert "STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS" in source
    assert "STOCKBOARD_BACKGROUND_REBUILD_POLL_MS" in source
    assert "STOCKBOARD_STATUS_WRITE_INTERVAL_SEC" in source
    assert "stockboard-candidate-background" in source
    assert "opening_burst_background_build_inflight" in source
    assert "opening_burst_background_coalesced_request_count" in source
    assert "opening_burst_cache_last_build_ms" in source
    assert "opening_burst_cache_last_overlay_ms" in source
