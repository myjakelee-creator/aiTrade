from __future__ import annotations

import threading
import time
from pathlib import Path

from realtime_v2.theme_board_patch import ThemeCacheService


class FakeState:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"event_count": 1, "market_phase": "regular", "market_phase_label": "정규장"}
        self.quotes = {
            "000660": {
                "stock_code": "000660", "stock_name": "SK하이닉스", "price": 270000,
                "change_rate": 5.0, "trade_value_eok": 1000, "amount_ratio": 4.0,
                "execution_strength": 180, "strength_5m": 170, "program_net": 100,
                "large_trade_net_count": 10, "received_at": "now", "price_age_sec": 0.1,
            },
            "042700": {
                "stock_code": "042700", "stock_name": "한미반도체", "price": 190000,
                "change_rate": 6.0, "trade_value_eok": 500, "amount_ratio": 5.0,
                "execution_strength": 190, "strength_5m": 180, "program_net": 50,
                "large_trade_net_count": 7, "received_at": "now", "price_age_sec": 0.1,
            },
            "058470": {
                "stock_code": "058470", "stock_name": "리노공업", "price": 300000,
                "change_rate": 4.0, "trade_value_eok": 300, "amount_ratio": 3.0,
                "execution_strength": 160, "strength_5m": 150, "program_net": 20,
                "large_trade_net_count": 3, "received_at": "now", "price_age_sec": 0.1,
            },
        }

    def snapshot(self):
        raise AssertionError("theme cache must not call state.snapshot")

    def rows(self):
        raise AssertionError("theme cache must not call state.rows")


def master_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "stockboard_theme_master.json"


def test_zero_clients_do_not_trigger_background_compute():
    service = ThemeCacheService(FakeState(), master_path(), interval_sec=0.25)
    service.start()
    try:
        time.sleep(0.35)
        assert service.compute_attempt_count == 0
        assert service.compute_success_count == 0
    finally:
        service.stop()
        service.join(timeout=2)


def test_multiple_clients_share_one_cached_compute():
    state = FakeState()
    service = ThemeCacheService(state, master_path(), interval_sec=0.5)
    for _ in range(5):
        service.register_client()
    try:
        assert service.refresh(force=True) is True
        first_success = service.compute_success_count
        for _ in range(5):
            service.refresh(force=False)
        assert first_success == 1
        assert service.compute_success_count == 1
        payload, body = service.get_snapshot()
        assert payload["status"]["state"] == "READY"
        assert body.startswith(b"{")
        assert payload["status"]["payload_bytes"] < 40_000
    finally:
        for _ in range(5):
            service.unregister_client()


def test_event_change_refreshes_shared_cache_once():
    state = FakeState()
    service = ThemeCacheService(state, master_path(), interval_sec=0.25)
    service.register_client()
    try:
        service.refresh(force=True)
        first_version = service.cache_version
        with state.lock:
            state.status["event_count"] += 1
            state.quotes["000660"]["trade_value_eok"] += 50
        service.last_check_mono -= 1.0
        assert service.refresh(force=False) is True
        assert service.cache_version == first_version + 1
        detail, detail_body = service.get_detail("HBM_EQUIPMENT")
        assert detail is not None
        assert detail_body is not None
        assert detail["theme"]["theme_id"] == "HBM_EQUIPMENT"
    finally:
        service.unregister_client()


def test_patch_install_starts_and_stops_fail_open_service():
    from types import SimpleNamespace

    from realtime_v2.theme_board_patch import install

    class FakeWebServer:
        def __init__(self, _server_address, _handler_class, state):
            self.state = state
            self.closed = False

        def server_close(self):
            self.closed = True

    class FakeWebHandler:
        def do_GET(self):
            self.original_called = True

    root = Path(__file__).resolve().parents[1]
    base = SimpleNamespace(ROOT=root, WebServer=FakeWebServer, WebHandler=FakeWebHandler)
    install(base)
    server = base.WebServer(("127.0.0.1", 0), base.WebHandler, FakeState())
    try:
        assert server.theme_cache_service.is_alive()
        assert server.theme_cache_service.compute_attempt_count == 0
    finally:
        server.server_close()
    assert server.closed is True
    assert not server.theme_cache_service.is_alive()


def test_invalid_master_is_isolated(tmp_path):
    invalid = tmp_path / "invalid_theme_master.json"
    invalid.write_text('{"schema_version":1,"themes":[]}', encoding="utf-8")
    service = ThemeCacheService(FakeState(), invalid)
    assert service.enabled is False
    payload, _body = service.get_snapshot(force_if_empty=False)
    assert payload["status"]["state"] == "INVALID_MASTER"
    assert service.last_error


def test_webserver_init_remains_fail_open_when_theme_service_raises(monkeypatch):
    from types import SimpleNamespace
    import realtime_v2.theme_board_patch as patch

    class FakeWebServer:
        def __init__(self, _server_address, _handler_class, state):
            self.state = state
            self.closed = False
        def server_close(self):
            self.closed = True

    class FakeWebHandler:
        def do_GET(self):
            self.original_called = True

    class BrokenService:
        def __init__(self, *_args, **_kwargs):
            raise RuntimeError("synthetic theme service failure")

    monkeypatch.setattr(patch, "ThemeCacheService", BrokenService)
    base = SimpleNamespace(ROOT=Path(__file__).resolve().parents[1], WebServer=FakeWebServer, WebHandler=FakeWebHandler)
    patch.install(base)
    state = FakeState()
    server = base.WebServer(("127.0.0.1", 0), base.WebHandler, state)
    assert server.theme_cache_service is None
    assert "synthetic theme service failure" in state.status["theme_board_last_error"]
    server.server_close()
    assert server.closed is True


def test_theme_http_routes_serve_cached_payloads():
    import json
    import urllib.request
    from http import HTTPStatus
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from types import SimpleNamespace

    from realtime_v2.theme_board_patch import install

    class RealWebServer(ThreadingHTTPServer):
        def __init__(self, server_address, handler_class, state):
            super().__init__(server_address, handler_class)
            self.state = state

    class RealWebHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *args):
            return

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._json({"error": "original not found"}, status=HTTPStatus.NOT_FOUND)

    base = SimpleNamespace(
        ROOT=Path(__file__).resolve().parents[1],
        WebServer=RealWebServer,
        WebHandler=RealWebHandler,
    )
    install(base)
    server = base.WebServer(("127.0.0.1", 0), base.WebHandler, FakeState())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(root + "/theme", timeout=3) as response:
            html = response.read().decode("utf-8")
        assert "ThemeBoard v1" in html
        assert "@media(max-width:900px)" in html

        with urllib.request.urlopen(root + "/api/v2/themes/status", timeout=3) as response:
            status = json.loads(response.read().decode("utf-8"))
        assert status["enabled"] is True

        with urllib.request.urlopen(root + "/api/v2/themes/snapshot", timeout=3) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
        assert snapshot["status"]["state"] == "READY"
        assert snapshot["themes"]

        detail_url = root + "/api/v2/themes/detail?theme_id=HBM_EQUIPMENT"
        with urllib.request.urlopen(detail_url, timeout=3) as response:
            detail = json.loads(response.read().decode("utf-8"))
        assert detail["theme"]["theme_id"] == "HBM_EQUIPMENT"
        assert detail["members"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_unchanged_event_skips_before_worker_lock_copy(monkeypatch):
    state = FakeState()
    service = ThemeCacheService(state, master_path(), interval_sec=0.25)
    service.register_client()
    try:
        assert service.refresh(force=True) is True
        service.last_check_mono -= 1.0

        def forbidden_copy():
            raise AssertionError("unchanged event must skip before quote copy")

        monkeypatch.setattr(service, "copy_rows", forbidden_copy)
        assert service.refresh(force=False) is False
        assert service.compute_attempt_count == 1
        assert service.compute_success_count == 1
    finally:
        service.unregister_client()


def test_busy_worker_lock_is_skipped_without_blocking():
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
        started = time.perf_counter()
        assert service.refresh(force=True) is False
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1
        assert service.lock_busy_skip_count == 1
        assert service.last_lock_wait_ms is not None
        assert service.last_lock_wait_ms < 50
        assert service.compute_success_count == 0
    finally:
        release.set()
        holder.join(timeout=2)
        service.unregister_client()
