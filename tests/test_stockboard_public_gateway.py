from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.public_gateway import (  # noqa: E402
    PublicDataCache,
    PublicGatewayHandler,
    PublicGatewayServer,
    SlidingWindowRateLimiter,
    build_public_html,
    contains_forbidden_key,
    sanitize_context,
    sanitize_snapshot,
)


class FakeCache:
    def __init__(self):
        self.snapshot = sanitize_snapshot(
            {
                "ts": "2026-07-21T20:00:00+09:00",
                "trading_date": "20260721",
                "status": {
                    "market_phase": "closed",
                    "market_phase_label": "장마감",
                    "pid": 1234,
                    "collector_status": {"secret": True},
                },
                "market_session": {
                    "phase": "closed",
                    "phase_label": "장마감",
                    "trading_date": "20260721",
                    "reason": "internal rule",
                },
                "display_order": {"paused": True, "version": 99},
                "rows": [
                    {
                        "stock_code": "005930",
                        "stock_name": "삼성전자",
                        "rank": 1,
                        "price": 100000,
                        "change_rate": 1.23,
                        "trade_value_eok": 1000,
                        "candidate_score": 88.5,
                        "ohlc": {"open": 99000, "high": 101000, "low": 98000, "close": 100000, "source": "private"},
                        "source_code": "005930_AL",
                        "received_at": "private timestamp",
                    }
                ],
            }
        )
        self.context = sanitize_context(
            {
                "market_supply": {
                    "kospi": {
                        "market_index": 3000.1,
                        "market_change_rate": 0.5,
                        "advancers": 500,
                        "decliners": 300,
                        "source": "private-file",
                    }
                },
                "us_market": {
                    "values": {
                        "NQ=F": {"change_rate": 0.7, "source": "private-api"},
                    }
                },
                "ohlc_snapshot_status": {"source": "C:/private/path"},
            }
        )
        self.version = 1

    def health(self):
        return {
            "ok": True,
            "service": "stockboard_v2_public_gateway",
            "read_only": True,
            "upstream_ok": True,
            "snapshot_age_sec": 0.1,
            "ts": "2026-07-21T20:00:00+09:00",
        }

    def get_snapshot(self):
        return self.snapshot, self.version

    def wait_for_snapshot(self, after_version, timeout_sec):
        return self.snapshot, self.version

    def get_context(self):
        return self.context


class FakeUpstreamHandler(BaseHTTPRequestHandler):
    snapshot_hits = 0
    context_hits = 0

    def log_message(self, _format, *args):
        return

    def _json(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/v2/snapshot"):
            type(self).snapshot_hits += 1
            self._json({
                "ts": "2026-07-21T20:00:00+09:00",
                "trading_date": "20260721",
                "status": {"market_phase": "closed", "pid": 999},
                "rows": [{"stock_code": "005930", "stock_name": "삼성전자", "price": 100000, "source_code": "005930_AL"}],
            })
            return
        if self.path == "/api/v2/context":
            type(self).context_hits += 1
            self._json({"market_supply": {"kospi": {"market_index": 3000.0, "source": "private"}}})
            return
        self.send_error(404)


class FakeUpstreamFixture:
    def __init__(self):
        FakeUpstreamHandler.snapshot_hits = 0
        FakeUpstreamHandler.context_hits = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstreamHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class GatewayHttpFixture:
    def __init__(self):
        self.server = PublicGatewayServer(
            ("127.0.0.1", 0),
            PublicGatewayHandler,
            cache=FakeCache(),
            public_html=build_public_html("<html><head><title>StockBoard v2 Realtime</title></head><body></body></html>").encode("utf-8"),
            per_client_rate=1000,
            global_rate=10000,
            max_concurrent_requests=16,
            max_stream_clients=4,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class PublicGatewaySanitizerTests(unittest.TestCase):
    def test_snapshot_allowlist_removes_private_diagnostics(self):
        raw = {
            "ts": "2026-07-21T20:00:00+09:00",
            "trading_date": "20260721",
            "status": {
                "market_phase": "regular",
                "market_phase_label": "정규장",
                "pid": 999,
                "daily_state_path": "C:/secret.json",
                "collector_status": {"raw": "secret"},
                "last_error": "private",
            },
            "market_session": {
                "phase": "regular",
                "phase_label": "정규장",
                "trading_date": "20260721",
                "reason": "private",
            },
            "display_order": {"paused": False, "version": 10},
            "rows": [
                {
                    "stock_code": "000660",
                    "stock_name": "SK하이닉스",
                    "rank": 1,
                    "price": 123000,
                    "candidate_grade_text": "A",
                    "grade_score": 92,
                    "ohlc": {"open": 120000, "high": 124000, "low": 119000, "close": 123000, "source": "private"},
                    "source_code": "000660_AL",
                    "program_net_source": "private source",
                    "received_at": "private timestamp",
                }
            ],
        }
        public = sanitize_snapshot(raw)
        self.assertEqual(public["source"], "stockboard_v2_public_gateway")
        self.assertEqual(public["rows"][0]["stock_code"], "000660")
        self.assertEqual(public["rows"][0]["ohlc"]["close"], 123000)
        self.assertNotIn("source", public["rows"][0]["ohlc"])
        self.assertNotIn("source_code", public["rows"][0])
        self.assertNotIn("received_at", public["rows"][0])
        self.assertNotIn("pid", public["status"])
        self.assertFalse(contains_forbidden_key(public))

    def test_context_allowlist_removes_paths_and_sources(self):
        public = sanitize_context(
            {
                "ts": "2026-07-21T20:00:00+09:00",
                "market_supply": {
                    "values": {
                        "KOSPI": {
                            "market_index": 3000.5,
                            "market_change_rate": 1.1,
                            "advancers": 600,
                            "decliners": 200,
                            "source": "C:/private/file.json",
                        }
                    }
                },
                "us_market": {
                    "values": {
                        "NQ=F": {"change_rate": 0.8, "error": "private"},
                        "SECRET": {"change_rate": 99},
                    }
                },
                "ohlc_snapshot_status": {"source": "C:/private/file.json"},
            }
        )
        self.assertEqual(public["market_supply"]["kospi"]["market_index"], 3000.5)
        self.assertNotIn("source", public["market_supply"]["kospi"])
        self.assertEqual(public["us_market"]["values"]["NQ=F"]["change_rate"], 0.8)
        self.assertNotIn("SECRET", public["us_market"]["values"])
        self.assertFalse(contains_forbidden_key(public))

    def test_public_html_marks_read_only_and_hides_controls(self):
        result = build_public_html("<html><head><title>StockBoard v2 Realtime</title></head><body></body></html>")
        self.assertIn("StockBoard v2 Public", result)
        self.assertIn("공개 읽기 전용", result)
        self.assertIn("row-position-toggle", result)
        self.assertIn("서버 제어 기능 없음", result)
        self.assertIn("writeClipboardText(text)", result)

    def test_rate_limiter_rejects_after_limit(self):
        limiter = SlidingWindowRateLimiter(per_client_per_minute=2, global_per_minute=10)
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))

    def test_cache_fetches_private_worker_once_and_keeps_only_public_fields(self):
        upstream = FakeUpstreamFixture()
        cache = PublicDataCache(upstream.url, snapshot_interval_sec=0.2, context_interval_sec=5.0)
        try:
            cache.refresh_snapshot()
            cache.refresh_context()
            snapshot, version = cache.get_snapshot()
            self.assertEqual(version, 1)
            self.assertEqual(FakeUpstreamHandler.snapshot_hits, 1)
            self.assertEqual(FakeUpstreamHandler.context_hits, 1)
            self.assertEqual(snapshot["rows"][0]["stock_code"], "005930")
            self.assertNotIn("source_code", snapshot["rows"][0])
            self.assertNotIn("pid", snapshot["status"])
            self.assertEqual(cache.get_context()["market_supply"]["kospi"]["market_index"], 3000.0)
            health = cache.health()
            self.assertTrue(health["upstream_ok"])
            self.assertTrue(health["context_ok"])
            self.assertNotIn("last_error", health)
        finally:
            cache.stop()
            upstream.close()

    def test_upstream_must_be_loopback_http(self):
        with self.assertRaises(ValueError):
            PublicDataCache("https://example.com")


class PublicGatewayHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GatewayHttpFixture()

    def tearDown(self):
        self.fixture.close()

    def _json(self, path):
        with urlopen(self.fixture.base_url + path, timeout=2) as response:
            return response.status, dict(response.headers), json.loads(response.read().decode("utf-8"))

    def test_health_has_security_headers_and_no_pid(self):
        status, headers, payload = self._json("/api/v2/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["read_only"])
        self.assertNotIn("pid", payload)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("default-src", headers.get("Content-Security-Policy", ""))
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_snapshot_is_sanitized(self):
        status, _headers, payload = self._json("/api/v2/snapshot?limit=300&candidate_model=anything")
        self.assertEqual(status, 200)
        self.assertEqual(payload["source"], "stockboard_v2_public_gateway")
        self.assertFalse(contains_forbidden_key(payload))
        self.assertEqual(payload["rows"][0]["stock_code"], "005930")

    def test_server_control_endpoint_is_forbidden(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.fixture.base_url + "/api/v2/display_order?mode=freeze", timeout=2)
        self.assertEqual(raised.exception.code, 403)

    def test_write_methods_are_rejected(self):
        request = Request(self.fixture.base_url + "/api/v2/snapshot", data=b"{}", method="POST")
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=2)
        self.assertEqual(raised.exception.code, 405)

    def test_unknown_endpoint_is_not_exposed(self):
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.fixture.base_url + "/api/v2/internal-doctor", timeout=2)
        self.assertEqual(raised.exception.code, 404)

    def test_robots_disallows_indexing(self):
        with urlopen(self.fixture.base_url + "/robots.txt", timeout=2) as response:
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("Disallow: /", body)
            self.assertEqual(response.headers.get("X-Robots-Tag"), "noindex, nofollow, noarchive")

    def test_head_has_no_body(self):
        request = Request(self.fixture.base_url + "/api/v2/health", method="HEAD")
        with urlopen(request, timeout=2) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")


if __name__ == "__main__":
    unittest.main()
