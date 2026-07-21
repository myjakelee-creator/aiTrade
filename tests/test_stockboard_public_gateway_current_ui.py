from __future__ import annotations

import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from realtime_v2 import public_gateway as gateway

ROOT = Path(__file__).resolve().parents[1]


class CurrentUiHandler(BaseHTTPRequestHandler):
    html = (
        "<!doctype html><html><head><title>StockBoard v2 Realtime</title></head>"
        "<body><div>CURRENT-UI-COLUMN 1분대금 5분강도</div>"
        "<script>const stream='/api/v2/stream';</script></body></html>"
    )

    def log_message(self, _format, *args):
        return

    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        body = self.html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class CurrentUiFixture:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), CurrentUiHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class PublicGatewayCurrentUiTests(unittest.TestCase):
    def test_script_entry_can_import_package_from_project_root(self):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "realtime_v2" / "public_gateway.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("public read-only gateway", completed.stdout)

    def test_fetches_exact_runtime_html_then_adds_read_only_boundary(self):
        fixture = CurrentUiFixture()
        old_argv = list(sys.argv)
        try:
            sys.argv = ["public_gateway.py", "--upstream", fixture.url]
            html = gateway.load_current_public_html(None).decode("utf-8")
        finally:
            sys.argv = old_argv
            fixture.close()

        self.assertIn("CURRENT-UI-COLUMN", html)
        self.assertIn("1분대금", html)
        self.assertIn("5분강도", html)
        self.assertIn("StockBoard v2 Public", html)
        self.assertIn("공개 읽기 전용", html)
        self.assertIn("board-shell-tab:not(.active)", html)

    def test_current_column_fields_remain_in_explicit_allowlist(self):
        public = gateway.sanitize_snapshot(
            {
                "status": {"market_phase": "closed", "universe_count": 196},
                "rows": [
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "trade_value_1m_eok": 12.3,
                        "trade_value_prev_1m_eok": 10.0,
                        "trade_value_1m_ratio_pct": 123.0,
                        "trade_value_1m_quality": "exact",
                        "strength_5m": 145.2,
                        "source_code": "005930_AL",
                    }
                ],
            }
        )
        row = public["rows"][0]
        self.assertEqual(public["status"]["universe_count"], 196)
        self.assertEqual(row["trade_value_1m_eok"], 12.3)
        self.assertEqual(row["trade_value_1m_ratio_pct"], 123.0)
        self.assertEqual(row["strength_5m"], 145.2)
        self.assertNotIn("source_code", row)


if __name__ == "__main__":
    unittest.main()
