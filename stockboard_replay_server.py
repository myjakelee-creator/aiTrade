from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import sys
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_OUT_DIR = ROOT / "data" / "runtime" / "replay_output"
SERVER_NAME = "StockBoard Replay Server"
SAFETY_PAYLOAD = {
    "replay_only": True,
    "kiwoom_connection": False,
    "order_enabled": False,
    "live_api_reuse": False,
    "recommended_api_prefix": "/api/replay",
}


@dataclass
class ReplaySession:
    session_id: str
    frames_path: Path
    frames: list[dict[str, Any]]
    summary: dict[str, Any] | None = None
    index: dict[str, Any] | None = None

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def minutes(self) -> list[str]:
        return [str(frame.get("minute")) for frame in self.frames]

    @property
    def first_minute(self) -> str | None:
        return self.minutes[0] if self.frames else None

    @property
    def last_minute(self) -> str | None:
        return self.minutes[-1] if self.frames else None

    @property
    def default_model_id(self) -> str | None:
        if not self.frames:
            return None
        meta = self.frames[0].get("meta") or {}
        return meta.get("default_model_id")

    def frame_by_index(self, index: int) -> dict[str, Any] | None:
        if index < 0 or index >= len(self.frames):
            return None
        return self.frames[index]

    def frame_by_minute(self, minute: str) -> dict[str, Any] | None:
        for frame in self.frames:
            if str(frame.get("minute")) == minute:
                return frame
        return None

    def minute_records(self) -> list[dict[str, Any]]:
        records = []
        for frame in self.frames:
            meta = frame.get("meta") or {}
            records.append({
                "minute": frame.get("minute"),
                "minute_index": frame.get("minute_index"),
                "row_count": meta.get("row_count"),
                "raw_default_count": meta.get("raw_candidate_count_default"),
                "stable_default_count": meta.get("stable_candidate_count_default"),
            })
        return records

    def metadata(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "frame_count": self.frame_count,
            "first_minute": self.first_minute,
            "last_minute": self.last_minute,
            "default_model_id": self.default_model_id,
            "frames_path": str(self.frames_path),
            "summary": self.summary,
            "safety": SAFETY_PAYLOAD,
        }


def json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def related_json_path(frames_path: Path, suffix: str) -> Path:
    name = frames_path.name.replace("_stockboard_frames.jsonl", suffix)
    return frames_path.with_name(name)


def load_session(frames_path: Path) -> ReplaySession:
    frames: list[dict[str, Any]] = []
    with frames_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                frame = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid frame JSONL: {frames_path}:{line_no}: {exc}") from exc
            if not isinstance(frame, dict):
                raise RuntimeError(f"Invalid frame payload type: {frames_path}:{line_no}")
            frames.append(frame)

    if not frames:
        raise RuntimeError(f"No frames in {frames_path}")

    session_id = str(frames[0].get("session_id") or frames_path.stem.replace("_stockboard_frames", ""))
    summary = json_file(related_json_path(frames_path, "_stockboard_frame_summary.json"))
    index = json_file(related_json_path(frames_path, "_stockboard_frame_index.json"))

    return ReplaySession(
        session_id=session_id,
        frames_path=frames_path,
        frames=frames,
        summary=summary,
        index=index,
    )


def discover_sessions(out_dir: Path) -> dict[str, ReplaySession]:
    sessions: dict[str, ReplaySession] = {}
    paths = sorted(
        out_dir.glob("*_stockboard_frames.jsonl"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    errors: list[str] = []
    for path in paths:
        try:
            session = load_session(path)
            sessions[session.session_id] = session
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: {exc}")

    if not sessions:
        detail = "; ".join(errors) if errors else f"No *_stockboard_frames.jsonl in {out_dir}"
        raise RuntimeError(detail)

    return sessions


class ReplayHTTPServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        sessions: dict[str, ReplaySession],
        default_session_id: str,
        out_dir: Path,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.sessions = sessions
        self.default_session_id = default_session_id
        self.out_dir = out_dir


class ReplayHandler(BaseHTTPRequestHandler):
    server_version = "StockBoardReplay/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("%s - - [%s] %s\n" % (self.client_address[0], self.log_date_time_string(), fmt % args))
        sys.stdout.flush()

    def _send_bytes(
        self,
        data: bytes,
        status: int = 200,
        content_type: str = "application/octet-stream",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send_bytes(data, status=status, content_type="application/json; charset=utf-8")

    def _send_text(self, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> None:
        self._send_bytes(text.encode("utf-8"), status=status, content_type=content_type)

    def _send_error_json(self, status: int, message: str, **extra: Any) -> None:
        payload = {
            "ok": False,
            "error": message,
            "status": status,
            "safety": SAFETY_PAYLOAD,
        }
        payload.update(extra)
        self._send_json(payload, status=status)

    def do_OPTIONS(self) -> None:
        self._send_bytes(b"", status=204, content_type="text/plain")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        try:
            if path == "/" or path == "/index.html":
                self.handle_root()
            elif path == "/api/replay/health":
                self.handle_health()
            elif path == "/api/replay/sessions":
                self.handle_sessions()
            elif path == "/api/replay/minutes":
                self.handle_minutes(query)
            elif path == "/api/replay/summary":
                self.handle_summary(query)
            elif path == "/api/replay/frame":
                self.handle_frame(query)
            elif path == "/api/replay/frames":
                self.handle_frames(query)
            elif path == "/favicon.ico":
                self._send_bytes(b"", status=204, content_type="image/x-icon")
            else:
                self._send_error_json(HTTPStatus.NOT_FOUND, f"Unknown replay endpoint: {path}")
        except Exception as exc:  # noqa: BLE001
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def get_session(self, query: dict[str, list[str]]) -> ReplaySession | None:
        session_id = first_query(query, "session") or self.server.default_session_id
        return self.server.sessions.get(session_id)

    def handle_root(self) -> None:
        html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <title>{SERVER_NAME}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 32px; line-height: 1.5; }}
    code {{ background: #f2f2f2; padding: 2px 4px; border-radius: 4px; }}
    .warn {{ color: #b00020; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>{SERVER_NAME}</h1>
  <p class="warn">REPLAY ONLY / NO KIWOOM / NO ORDER / NO LIVE API REUSE</p>
  <p>Available endpoints:</p>
  <ul>
    <li><code>/api/replay/health</code></li>
    <li><code>/api/replay/sessions</code></li>
    <li><code>/api/replay/minutes</code></li>
    <li><code>/api/replay/frame?index=0</code></li>
    <li><code>/api/replay/frame?minute=YYYY-MM-DDTHH:MM</code></li>
    <li><code>/api/replay/summary</code></li>
  </ul>
</body>
</html>
"""
        self._send_text(html, content_type="text/html; charset=utf-8")

    def handle_health(self) -> None:
        self._send_json({
            "ok": True,
            "server": SERVER_NAME,
            "default_session_id": self.server.default_session_id,
            "session_count": len(self.server.sessions),
            "out_dir": str(self.server.out_dir),
            "safety": SAFETY_PAYLOAD,
        })

    def handle_sessions(self) -> None:
        sessions = [session.metadata() for session in self.server.sessions.values()]
        sessions.sort(key=lambda item: str(item.get("session_id")))
        self._send_json({
            "ok": True,
            "default_session_id": self.server.default_session_id,
            "sessions": sessions,
            "safety": SAFETY_PAYLOAD,
        })

    def handle_minutes(self, query: dict[str, list[str]]) -> None:
        session = self.get_session(query)
        if session is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Replay session not found")
            return
        self._send_json({
            "ok": True,
            "session_id": session.session_id,
            "frame_count": session.frame_count,
            "first_minute": session.first_minute,
            "last_minute": session.last_minute,
            "minutes": session.minute_records(),
            "safety": SAFETY_PAYLOAD,
        })

    def handle_summary(self, query: dict[str, list[str]]) -> None:
        session = self.get_session(query)
        if session is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Replay session not found")
            return
        self._send_json({
            "ok": True,
            "session": session.metadata(),
            "safety": SAFETY_PAYLOAD,
        })

    def handle_frame(self, query: dict[str, list[str]]) -> None:
        session = self.get_session(query)
        if session is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Replay session not found")
            return

        frame: dict[str, Any] | None = None
        minute = first_query(query, "minute")
        index_text = first_query(query, "index")

        if minute:
            frame = session.frame_by_minute(minute)
        elif index_text is not None:
            try:
                frame = session.frame_by_index(int(index_text))
            except ValueError:
                self._send_error_json(HTTPStatus.BAD_REQUEST, f"Invalid index: {index_text}")
                return
        else:
            frame = session.frame_by_index(0)

        if frame is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Replay frame not found")
            return

        payload = copy.deepcopy(frame)
        model = first_query(query, "model")
        if model:
            candidates = payload.get("candidates") or {}
            raw_by_model = candidates.get("raw_by_model") or {}
            stable_by_model = candidates.get("stable_by_model") or {}
            candidates["default_model_id"] = model
            candidates["raw_default"] = raw_by_model.get(model, [])
            candidates["stable_default"] = stable_by_model.get(model, [])
            payload["candidates"] = candidates

        payload["ok"] = True
        payload["safety"] = SAFETY_PAYLOAD
        self._send_json(payload)

    def handle_frames(self, query: dict[str, list[str]]) -> None:
        session = self.get_session(query)
        if session is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Replay session not found")
            return

        limit = parse_positive_int(first_query(query, "limit"), default=10)
        offset = parse_positive_int(first_query(query, "offset"), default=0)
        frames = session.frames[offset:offset + limit]

        self._send_json({
            "ok": True,
            "session_id": session.session_id,
            "offset": offset,
            "limit": limit,
            "returned": len(frames),
            "frame_count": session.frame_count,
            "frames": frames,
            "safety": SAFETY_PAYLOAD,
        })


def first_query(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0]
    return value if value != "" else None


def parse_positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= 0 else default


def choose_default_session_id(sessions: dict[str, ReplaySession], requested: str | None = None) -> str:
    if requested and requested in sessions:
        return requested
    newest = sorted(
        sessions.values(),
        key=lambda session: session.frames_path.stat().st_mtime,
        reverse=True,
    )[0]
    return newest.session_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only StockBoard replay API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--session", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    sessions = discover_sessions(out_dir)
    default_session_id = choose_default_session_id(sessions, args.session)

    server = ReplayHTTPServer(
        (args.host, args.port),
        ReplayHandler,
        sessions=sessions,
        default_session_id=default_session_id,
        out_dir=out_dir,
    )

    print("STOCKBOARD_REPLAY_SERVER_START")
    print(f"host: {args.host}")
    print(f"port: {args.port}")
    print(f"default_session_id: {default_session_id}")
    print(f"session_count: {len(sessions)}")
    print("safety:", SAFETY_PAYLOAD)
    print(f"health: http://{args.host}:{args.port}/api/replay/health")
    print(f"sessions: http://{args.host}:{args.port}/api/replay/sessions")
    print(f"frame0: http://{args.host}:{args.port}/api/replay/frame?index=0")
    sys.stdout.flush()

    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("STOCKBOARD_REPLAY_SERVER_STOP")
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())