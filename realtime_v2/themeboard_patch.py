from __future__ import annotations

import json
import time
import traceback
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from stockboard_theme_cache import ThemeBoardCacheService

_MARKER = "stockboard_themeboard_patch_v1"


def _json_bytes(payload) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def install(base, large=None) -> None:
    if getattr(base, _MARKER, False):
        return

    original_init = base.State.__init__
    original_do_get = base.WebHandler.do_GET

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            self.theme_board_cache = ThemeBoardCacheService(self, autostart=True)
            self.status["theme_patch_installed"] = True
        except Exception as error:
            self.theme_board_cache = None
            self.status["theme_patch_installed"] = False
            self.status["theme_last_error"] = f"{type(error).__name__}: {error}"
            try:
                runtime = Path(getattr(base, "RUNTIME_DIR"))
                runtime.mkdir(parents=True, exist_ok=True)
                (runtime / "themeboard_patch_error.txt").write_text(
                    f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
                    encoding="utf-8",
                )
            except Exception:
                pass

    def send_json(handler, payload, status=HTTPStatus.OK):
        body = _json_bytes(payload)
        handler.send_response(int(status))
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def service_for(handler):
        state = getattr(handler.server, "state", None)
        return getattr(state, "theme_board_cache", None)

    def stream_themes(handler, query):
        service = service_for(handler)
        if service is None:
            send_json(handler, {"error": "theme cache unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        try:
            interval_ms = int(query.get("interval_ms", ["250"])[0])
        except (TypeError, ValueError):
            interval_ms = 250
        interval_sec = max(0.2, min(2.0, interval_ms / 1000.0))
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        last_version = None
        last_sent_at = 0.0
        try:
            while True:
                payload = service.snapshot(include_details=False)
                version = payload.get("version")
                now = time.monotonic()
                if version != last_version or now - last_sent_at >= 2.0:
                    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    handler.wfile.write(f"event: themes\ndata: {body}\n\n".encode("utf-8"))
                    handler.wfile.flush()
                    last_version = version
                    last_sent_at = now
                time.sleep(interval_sec)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def patched_do_get(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path in {"/themeboard", "/themeboard/", "/themeboard.html"}:
            html_path = Path(base.ROOT) / "docs" / "themeboard.html"
            if not html_path.is_file():
                send_json(self, {"error": "themeboard html not found"}, HTTPStatus.NOT_FOUND)
                return
            body = html_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/v2/themes":
            service = service_for(self)
            if service is None:
                send_json(self, {"error": "theme cache unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            send_json(self, service.snapshot(include_details=False))
            return
        if parsed.path == "/api/v2/theme":
            service = service_for(self)
            if service is None:
                send_json(self, {"error": "theme cache unavailable"}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            theme_id = str(query.get("theme_id", [""])[0]).strip().upper()
            detail = service.theme_detail(theme_id)
            if detail is None:
                send_json(self, {"error": "theme not found", "theme_id": theme_id}, HTTPStatus.NOT_FOUND)
                return
            send_json(
                self,
                {
                    "schema_version": 1,
                    "source": "stockboard_theme_cache",
                    "version": service.cache_version,
                    "updated_at": service.cache.get("updated_at"),
                    "theme": detail,
                },
            )
            return
        if parsed.path == "/api/v2/theme_stream":
            stream_themes(self, query)
            return
        return original_do_get(self)

    base.State.__init__ = patched_init
    base.WebHandler.do_GET = patched_do_get
    setattr(base, _MARKER, True)
