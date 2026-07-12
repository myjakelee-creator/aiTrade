from __future__ import annotations

import json
import time
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .assets import BOARDS_HTML, SHELL_CSS, SHELL_JS
from .performance import BoardPerformanceService
from .registry import registry_payload
from .stockboard_cache import StockBoardSnapshotCacheService

_MARKER = "BOARD_PLATFORM_SHELL_V1"


def inject_shell_assets(html: str, board_id: str) -> str:
    if _MARKER in html:
        return html
    head = (
        f'<meta name="board-id" content="{board_id}">'
        '<link rel="stylesheet" href="/api/v2/boards/shell.css">'
        f'<!-- {_MARKER} -->'
    )
    script = '<script src="/api/v2/boards/shell.js"></script>'
    if "</head>" in html:
        html = html.replace("</head>", f"{head}</head>", 1)
    else:
        html = head + html
    if "</body>" in html:
        html = html.replace("</body>", f"{script}</body>", 1)
    else:
        html += script
    return html


def _send_bytes(handler, body: bytes, content_type: str, status: int = 200) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_json(handler, payload, status: int = 200) -> None:
    if hasattr(handler, "_json"):
        handler._json(payload, status=status)
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _send_bytes(handler, body, "application/json; charset=utf-8", status)


def _stream_shared(handler, service: StockBoardSnapshotCacheService, query) -> None:
    try:
        limit = int((query.get("limit") or ["300"])[0])
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(1000, limit))
    model_id = str(
        (query.get("candidate_model") or query.get("candidateModel") or [""])[0]
    ).strip()
    if model_id:
        service.set_candidate_model(model_id)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()

    service.register_client()
    state = handler.server.state
    state_lock = getattr(state, "lock", None)
    if state_lock is not None:
        with state_lock:
            state.status["stream_clients"] = int(state.status.get("stream_clients") or 0) + 1

    last_version = -1
    last_sent_at = 0.0
    try:
        service.request_refresh(force=not bool(service.payload_bytes))
        while True:
            now = time.monotonic()
            version, body = service.get_bytes()
            if limit != 300:
                payload = service.get_payload(
                    limit=limit,
                    refresh_if_changed=False,
                )
                body = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            if version != last_version:
                handler.wfile.write(b"event: snapshot\ndata: " + body + b"\n\n")
                handler.wfile.flush()
                last_version = version
                last_sent_at = now
            elif now - last_sent_at >= 2.0:
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
                last_sent_at = now
            time.sleep(0.05)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        service.unregister_client()
        if state_lock is not None:
            with state_lock:
                state.status["stream_clients"] = max(
                    0, int(state.status.get("stream_clients") or 1) - 1
                )


def install(base, large) -> None:
    if getattr(base, "_board_platform_installed", False):
        return

    root = Path(getattr(base, "ROOT", Path(__file__).resolve().parents[2]))
    theme_html = root / "docs" / "stockboard_theme_v1.html"

    original_ui_patch = large._ui_safety_patch

    def platform_ui_patch(html: str) -> str:
        return inject_shell_assets(original_ui_patch(html), "stockboard")

    large._ui_safety_patch = platform_ui_patch

    original_server_init = base.WebServer.__init__
    original_server_close = base.WebServer.server_close
    original_do_get = base.WebHandler.do_GET
    original_stream = base.WebHandler._stream_snapshots
    original_write_status_loop = base.write_status_loop

    def patched_server_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        try:
            stock_cache = StockBoardSnapshotCacheService(
                state,
                encoder=getattr(base, "safe_json_dumps", None),
            )
            self.stockboard_snapshot_cache = stock_cache
            state.stockboard_snapshot_cache = stock_cache
            stock_cache.start()
        except Exception as error:
            self.stockboard_snapshot_cache = None
            state.status["stockboard_cache_last_error"] = (
                f"{type(error).__name__}: {error}"
            )
        try:
            performance = BoardPerformanceService(self)
            self.board_performance_service = performance
            performance.start()
        except Exception as error:
            self.board_performance_service = None
            state.status["board_performance_last_error"] = (
                f"{type(error).__name__}: {error}"
            )

    def patched_server_close(self):
        for name in ("board_performance_service", "stockboard_snapshot_cache"):
            service = getattr(self, name, None)
            if service is not None:
                try:
                    service.stop()
                    if service.is_alive():
                        service.join(timeout=2)
                except Exception:
                    pass
        return original_server_close(self)

    def patched_stream(self, query):
        service = getattr(self.server, "stockboard_snapshot_cache", None)
        if service is None:
            return original_stream(self, query)
        return _stream_shared(self, service, query)

    def patched_get(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/v2/boards/shell.css":
            _send_bytes(
                self, SHELL_CSS.encode("utf-8"), "text/css; charset=utf-8"
            )
            return
        if parsed.path == "/api/v2/boards/shell.js":
            _send_bytes(
                self,
                SHELL_JS.encode("utf-8"),
                "application/javascript; charset=utf-8",
            )
            return
        if parsed.path == "/boards":
            _send_bytes(
                self, BOARDS_HTML.encode("utf-8"), "text/html; charset=utf-8"
            )
            return
        if parsed.path == "/api/v2/boards":
            _send_json(self, registry_payload(self.server))
            return
        if parsed.path == "/api/v2/boards/performance":
            board_id = str((query.get("board_id") or [""])[0]).strip()
            service = getattr(self.server, "board_performance_service", None)
            payload = (
                service.get_payload(board_id)
                if service is not None
                else {
                    "error": "performance service unavailable",
                    "display_metrics": [],
                }
            )
            _send_json(
                self,
                payload,
                200 if service is not None else HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        if parsed.path in {"/theme", "/stockboard_theme_v1.html"}:
            try:
                html = inject_shell_assets(
                    theme_html.read_text(encoding="utf-8-sig"), "themeboard"
                )
            except OSError as error:
                _send_json(self, {"error": str(error)}, HTTPStatus.NOT_FOUND)
                return
            _send_bytes(self, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/v2/snapshot":
            service = getattr(self.server, "stockboard_snapshot_cache", None)
            if service is not None:
                model_id = str(
                    (
                        query.get("candidate_model")
                        or query.get("candidateModel")
                        or [""]
                    )[0]
                ).strip()
                if model_id:
                    service.set_candidate_model(model_id)
                try:
                    limit = int((query.get("limit") or ["300"])[0])
                except (TypeError, ValueError):
                    limit = 300
                payload = service.get_payload(
                    limit=max(1, min(1000, limit)),
                    refresh_if_changed=True,
                )
                _send_json(self, payload)
                return
        if parsed.path == "/api/v2/stream":
            return patched_stream(self, query)
        return original_do_get(self)

    def patched_write_status_loop(state, output_path, stop_event):
        while not stop_event.wait(1.0):
            try:
                service = getattr(state, "stockboard_snapshot_cache", None)
                if service is not None:
                    payload = service.get_payload(
                        limit=300,
                        force_if_empty=True,
                        refresh_if_changed=True,
                    )
                else:
                    payload = state.snapshot(limit=300)
                base.atomic_write_json(output_path, payload)
                state.persist_daily_state_if_needed()
            except Exception:
                pass

    base.WebServer.__init__ = patched_server_init
    base.WebServer.server_close = patched_server_close
    base.WebHandler._stream_snapshots = patched_stream
    base.WebHandler.do_GET = patched_get
    base.write_status_loop = patched_write_status_loop
    base._board_platform_installed = True
