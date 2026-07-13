from __future__ import annotations

import threading
from copy import deepcopy
from types import SimpleNamespace

from realtime_v2.worker_opening_burst_cache_patch import install


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
            },
            "000002": {
                "stock_code": "000002",
                "price": 200,
                "trade_price": 200,
                "change_rate": -1.0,
                "received_at": "2026-07-13T09:00:00+09:00",
                "row_source": "realtime",
            },
        }
        self.selected_candidate_model_id = "model-a"
        self.heavy_builds = 0

    def logger_stats(self):
        return {"event_log_queue_size": 0}

    def persist_daily_state_if_needed(self):
        return None

    def snapshot(self, limit=300):
        self.heavy_builds += 1
        rows = []
        for code in sorted(self.quotes):
            quote = deepcopy(self.quotes[code])
            quote["candidate_score"] = self.heavy_builds
            quote["rank"] = len(rows) + 1
            rows.append(quote)
        return {
            "schema_version": 1,
            "source": "fake",
            "ts": "heavy-build",
            "status": deepcopy(self.status),
            "row_count": len(rows[:limit]),
            "rows": rows[:limit],
        }


def test_heavy_snapshot_is_reused_while_price_is_overlaid(monkeypatch):
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", "5000")
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", "10000")
    monkeypatch.setenv("STOCKBOARD_STATUS_WRITE_INTERVAL_SEC", "5")

    writes = []
    fake_base = SimpleNamespace(
        State=FakeState,
        write_status_loop=lambda *_args, **_kwargs: None,
        atomic_write_json=lambda path, payload: writes.append((path, payload)),
    )
    install(fake_base)

    state = FakeState()
    first = state.snapshot(limit=1)
    assert state.heavy_builds == 1
    assert first["rows"][0]["candidate_score"] == 1
    assert first["status"]["opening_burst_cache_hit"] is False

    with state.lock:
        state.quotes["000001"]["price"] = 101
        state.quotes["000001"]["trade_price"] = 101
        state.quotes["000001"]["change_rate"] = 1.1
        state.status["event_count"] += 1
        state.status["trade_count"] += 1

    second = state.snapshot(limit=1)
    assert state.heavy_builds == 1
    assert second["rows"][0]["price"] == 101
    assert second["rows"][0]["change_rate"] == 1.1
    assert second["rows"][0]["candidate_score"] == 1
    assert second["status"]["opening_burst_cache_hit"] is True
    assert second["status"]["opening_burst_cache_reuse_count"] >= 1


def test_candidate_model_change_forces_immediate_heavy_rebuild(monkeypatch):
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", "5000")
    monkeypatch.setenv("STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", "10000")

    fake_base = SimpleNamespace(
        State=FakeState,
        write_status_loop=lambda *_args, **_kwargs: None,
        atomic_write_json=lambda *_args, **_kwargs: None,
    )
    install(fake_base)

    state = FakeState()
    state.snapshot(limit=2)
    assert state.heavy_builds == 1

    state.selected_candidate_model_id = "model-b"
    payload = state.snapshot(limit=2)
    assert state.heavy_builds == 2
    assert payload["rows"][0]["candidate_score"] == 2
    assert payload["status"]["opening_burst_cache_hit"] is False


def test_patch_exposes_opening_burst_tuning_and_status_write_policy():
    source = __import__(
        "pathlib"
    ).Path("realtime_v2/worker_opening_burst_cache_patch.py").read_text(
        encoding="utf-8"
    )
    assert "STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS" in source
    assert "STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS" in source
    assert "STOCKBOARD_STATUS_WRITE_INTERVAL_SEC" in source
    assert "opening_burst_cache_last_build_ms" in source
    assert "opening_burst_cache_last_overlay_ms" in source
