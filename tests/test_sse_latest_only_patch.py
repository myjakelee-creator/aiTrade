from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from realtime_v2 import sse_latest_only_patch as patch


class FakeState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.status = {"event_count": 1, "stream_clients": 0}
        self.snapshot_calls = 0

    def snapshot(self, limit: int = 300):
        self.snapshot_calls += 1
        return {
            "ts": "2026-07-23T10:00:00.000+09:00",
            "status": dict(self.status),
            "rows": [{"stock_code": "005930", "price": 270000}],
            "row_count": 1,
            "requested_limit": limit,
        }


class StopAfterFirstWrite:
    def __init__(self) -> None:
        self.payload = b""

    def write(self, data: bytes) -> None:
        self.payload += data
        raise BrokenPipeError()

    def flush(self) -> None:
        pass


class FakeHandler:
    def __init__(self, state: FakeState) -> None:
        self.server = SimpleNamespace(state=state)
        self.wfile = StopAfterFirstWrite()
        self.headers = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.headers.append((key, value))

    def end_headers(self) -> None:
        pass


class FakeBase:
    WebHandler = FakeHandler

    @staticmethod
    def safe_json_dumps(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def now_text() -> str:
        return "2026-07-23T10:00:00.000+09:00"


def test_latest_only_stream_caps_rows_and_exposes_runtime_status():
    patch.install(FakeBase)
    state = FakeState()
    handler = FakeHandler(state)

    handler._stream_snapshots({"limit": ["300"], "interval_ms": ["100"]})

    assert handler.status == 200
    assert state.snapshot_calls == 1
    assert b"event: snapshot" in handler.wfile.payload
    assert state.status["sse_latest_only_version"] == "sse_latest_only_v1"
    assert state.status["sse_latest_only_send_interval_ms"] == 200
    assert state.status["sse_latest_only_row_limit"] == 100
    assert state.status["stream_clients"] == 0


def test_patch_contract_does_not_touch_market_data_sources():
    source = patch.__file__
    text = open(source, encoding="utf-8").read()
    for forbidden in (
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "issue_access_token",
        "websocket",
    ):
        assert forbidden not in text
