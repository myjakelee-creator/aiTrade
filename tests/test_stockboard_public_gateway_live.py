from __future__ import annotations

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2 import public_gateway_core as core
from realtime_v2.public_gateway_live import (
    CURRENT_UI_MARKER,
    GATEWAY_VERSION,
    PUBLIC_CHROME_CLEANUP_VERSION,
    PUBLIC_ROOT_CONTRACT_VERSION,
    LivePublicGatewayHandler,
    LivePublicGatewayServer,
    fetch_current_public_html,
)


class _UpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        return

    def do_GET(self):
        if self.path.startswith("/?"):
            body = (
                "<!doctype html><html><head><title>StockBoard v2 Realtime</title>"
                "<style>#topbar.topbar{height:112px;min-height:112px;max-height:112px}</style>"
                "</head><body><div id='topbar' class='topbar'>"
                "<div class='metric-row'><span class='title'>StockBoard v2 Realtime</span>"
                "<span id='status'>connected</span><span id='copy-status'>copy help</span></div>"
                "<div class='metric-row'><span id='counts'>rows</span>"
                "<span id='latency'>stream 12 ms</span>"
                "<span id='throughput'>recv/s 1</span>"
                "<span id='collector-metrics'>collector_q 0</span>"
                "<span id='worker-metrics'>worker_q 0</span>"
                "<span id='lag-metrics'>lag 0</span></div>"
                "<div class='metric-row'><span id='render-metrics'>render 0.6 ms</span>"
                "<span id='metric-mode-status'>metric help</span>"
                "<span class='small'>color help</span></div></div>"
                "<div>1분대금</div><div>5분강도</div>"
                "<script>const trade_value_1m_eok=1;const strength_5m=2;"
                "new EventSource('/api/v2/stream')</script></body></html>"
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)


class _UpstreamFixture:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class PublicGatewayLiveUiTests(unittest.TestCase):
    def test_fetches_exact_current_ui_and_adds_public_boundary(self):
        upstream = _UpstreamFixture()
        try:
            html = fetch_current_public_html(upstream.url).decode("utf-8")
            self.assertIn("1분대금", html)
            self.assertIn("5분강도", html)
            self.assertIn(CURRENT_UI_MARKER, html)
            self.assertIn(GATEWAY_VERSION, html)
            self.assertIn(PUBLIC_CHROME_CLEANUP_VERSION, html)
            self.assertIn(PUBLIC_ROOT_CONTRACT_VERSION, html)
            self.assertIn("공개 읽기 전용 · 현재 UI", html)
            self.assertIn("window.history.replaceState(null,'','/')", html)
            self.assertNotIn("StockBoard v2 Realtime</title>", html)
        finally:
            upstream.close()

    def test_exact_root_path_serves_current_ui_without_query_string(self):
        upstream = _UpstreamFixture()
        gateway = LivePublicGatewayServer(
            ("127.0.0.1", 0),
            LivePublicGatewayHandler,
            cache=object(),
            public_html=b"",
            per_client_rate=1000,
            global_rate=10000,
            max_concurrent_requests=16,
            max_stream_clients=4,
            live_upstream=upstream.url,
        )
        thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        thread.start()
        try:
            root_url = f"http://127.0.0.1:{gateway.server_address[1]}/"
            with urlopen(root_url, timeout=3) as response:
                html = response.read().decode("utf-8")
                headers = response.headers
            self.assertIn(PUBLIC_ROOT_CONTRACT_VERSION, html)
            self.assertIn("1분대금", html)
            self.assertIn("5분강도", html)
            self.assertIn("no-store", headers.get("Cache-Control", ""))
            self.assertEqual(headers.get("Pragma"), "no-cache")
            self.assertEqual(headers.get("Expires"), "0")
            self.assertEqual(headers.get("X-StockBoard-Public-Root"), "/")
            self.assertEqual(
                headers.get("X-StockBoard-Public-Root-Contract"),
                PUBLIC_ROOT_CONTRACT_VERSION,
            )
        finally:
            gateway.shutdown()
            gateway.server_close()
            thread.join(timeout=2)
            upstream.close()

    def test_public_diagnostic_chrome_is_removed_and_observed_on_mobile(self):
        upstream = _UpstreamFixture()
        try:
            html = fetch_current_public_html(upstream.url).decode("utf-8")
        finally:
            upstream.close()

        for selector in (
            "#copy-status",
            "#counts",
            "#latency",
            "#throughput",
            "#collector-metrics",
            "#worker-metrics",
            "#lag-metrics",
            "#render-metrics",
            "#metric-mode-status",
            "#topbar .small",
        ):
            self.assertIn(selector, html)

        for element_id in (
            "copy-status",
            "counts",
            "latency",
            "throughput",
            "collector-metrics",
            "worker-metrics",
            "lag-metrics",
            "render-metrics",
            "metric-mode-status",
        ):
            self.assertIn(f"'{element_id}'", html)

        self.assertIn("height:auto!important", html)
        self.assertIn("min-height:0!important", html)
        self.assertIn("max-height:none!important", html)
        self.assertIn("PUBLIC_REMOVE_IDS", html)
        self.assertIn("new MutationObserver(schedulePublicTopbarCleanup)", html)
        self.assertIn("observer.observe(topbar,{childList:true,subtree:true})", html)
        self.assertIn("orientationchange", html)
        self.assertIn("if(!visible) row.remove()", html)
        self.assertNotIn("label:has(#candidate-model-selector)", html)

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
