import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from stockboard_execution_chart import get_chart, get_index, save_upload

HOST = os.getenv("STOCKBOARD_EXEC_CHART_HOST", "127.0.0.1")
PORT = int(os.getenv("STOCKBOARD_EXEC_CHART_PORT", "8010"))
DOCS_DIR = Path(__file__).resolve().parent / "docs"


class ExecutionChartHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DOCS_DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        try:
            if path == "/api/execution_chart_index":
                self._send_json(get_index())
                return
            if path == "/api/execution_chart":
                date = (query.get("date") or [""])[0]
                code = (query.get("code") or [""])[0]
                self._send_json(get_chart(date, code))
                return
        except FileNotFoundError as error:
            self._send_json({"error": str(error)}, status=404)
            return
        except Exception as error:
            self._send_json({"error": str(error)}, status=500)
            return
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/execution_chart_upload":
            self._send_json({"error": "not found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
            content_type = self.headers.get("Content-Type") or ""
            body = self.rfile.read(length)
            result = save_upload(content_type, body)
            self._send_json({
                "saved": result.get("saved"),
                "item": result.get("item"),
                "index": result.get("index"),
                "summary": result.get("payload", {}).get("summary"),
            })
        except Exception as error:
            self._send_json({"error": str(error)}, status=400)


def main():
    if not DOCS_DIR.is_dir():
        raise RuntimeError(f"docs directory not found: {DOCS_DIR}")
    server = ThreadingHTTPServer((HOST, PORT), ExecutionChartHandler)
    print(
        f"StockBoard execution chart server: "
        f"http://{HOST}:{PORT}/stockboard_execution_chart_sample.html",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
