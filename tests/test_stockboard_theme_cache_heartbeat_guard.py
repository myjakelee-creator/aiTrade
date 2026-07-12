from __future__ import annotations

import threading
import time
from pathlib import Path

from realtime_v2.theme_board_patch import ThemeCacheService


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {
            "event_count": 1,
            "trade_count": 1,
            "market_phase": "closed",
            "market_phase_label": "마감",
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "stock_name": "SK하이닉스",
                "price": 270000,
                "change_rate": 5.0,
                "trade_value_eok": 1000,
                "amount_ratio": 4.0,
                "execution_strength": 180,
                "strength_5m": 170,
                "program_net": 100,
                "large_trade_net_count": 10,
                "received_at": "now",
                "price_age_sec": 0.1,
            }
        }


def master_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "stockboard_theme_master.json"


def test_heartbeat_event_count_does_not_probe_worker_lock(monkeypatch):
    state = FakeState()
    service = ThemeCacheService(state, master_path(), interval_sec=0.25)
    service.register_client()
    try:
        assert service.refresh(force=True) is True
        state.status["event_count"] += 100
        service.last_check_mono -= 1.0

        def forbidden_copy():
            raise AssertionError("heartbeat-only changes must not copy quotes")

        monkeypatch.setattr(service, "copy_rows", forbidden_copy)
        assert service.refresh(force=False) is False
        assert service.compute_attempt_count == 1
        assert service.compute_success_count == 1
    finally:
        service.unregister_client()


def test_worker_lock_probe_is_strictly_nonblocking():
    state = FakeState()
    service = ThemeCacheService(state, master_path(), interval_sec=0.25)
    service.register_client()
    locked = threading.Event()
    release = threading.Event()

    def hold_lock():
        with state.lock:
            locked.set()
            release.wait(1.0)

    holder = threading.Thread(target=hold_lock, daemon=True)
    holder.start()
    assert locked.wait(1.0)
    try:
        state.status["trade_count"] += 1
        service.last_check_mono -= 1.0
        started = time.perf_counter()
        assert service.refresh(force=False) is False
        elapsed_ms = (time.perf_counter() - started) * 1000
        assert elapsed_ms < 20
        assert service.last_lock_wait_ms == 0.0
        assert service.lock_busy_skip_count == 1
        assert service.compute_success_count == 0
    finally:
        release.set()
        holder.join(timeout=2)
        service.unregister_client()
