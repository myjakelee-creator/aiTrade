from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2 import public_gateway_core as core
from realtime_v2.public_gateway_live import (
    CURRENT_UI_MARKER,
    GATEWAY_VERSION,
    fetch_current_public_html,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        return

    def do_GET(self):
        if self.path.startswith("/?"):
            body = (
                "<!doctype html><html><head><title>StockBoard v2 Realtime</title></head>"
                "<body><div>1분대금</div><div>5분강도</div>"
                "<script>new EventSource('/api/v2/stream')</script></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


class PublicGatewayLiveUiTests(unittest.TestCase):
    def test_fetches_exact_current_ui_and_adds_public_boundary(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            html = fetch_current_public_html(url).decode("utf-8")
            self.assertIn("1분대금", html)
            self.assertIn("5분강도", html)
            self.assertIn(CURRENT_UI_MARKER, html)
            self.assertIn(GATEWAY_VERSION, html)
            self.assertIn("공개 읽기 전용 · 현재 UI", html)
            self.assertNotIn("StockBoard v2 Realtime</title>", html)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_current_public_fields_survive_allowlist(self):
        payload = core.sanitize_snapshot(
            {
                "rows": [
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "trade_value_1m_eok": 12.3,
                        "trade_value_1m_ratio_pct": 150,
                        "strength_5m": 123.4,
                        "source_code": "005930_AL",
                    }
                ]
            }
        )
        row = payload["rows"][0]
        self.assertEqual(row["trade_value_1m_eok"], 12.3)
        self.assertEqual(row["trade_value_1m_ratio_pct"], 150)
        self.assertEqual(row["strength_5m"], 123.4)
        self.assertNotIn("source_code", row)


if __name__ == "__main__":
    unittest.main()
