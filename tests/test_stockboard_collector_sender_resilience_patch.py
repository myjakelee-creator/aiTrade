from __future__ import annotations

import json
import threading
from types import SimpleNamespace

from realtime_v2.collector_sender_resilience_patch import install


class DummySender(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop_event = threading.Event()
        self.host = "127.0.0.1"
        self.port = 8710
        self.flush_sec = 0.01
        self.sent_count = 0
        self.last_flush_count = 0
        self.last_flush_at = None
        self.last_error = None
        self.connected = False
        self.requeued = []

    def stats(self):
        return {"connected": self.connected, "sent_count": self.sent_count}

    def _requeue_unsent(self, events):
        self.requeued.extend(events)

    def _update_rate(self):
        return None

    def _drain(self):
        return []


class DummySocket:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.payloads = []

    def sendall(self, payload):
        if self.fail:
            raise OSError("synthetic socket failure")
        self.payloads.append(payload)


class DummyBase(SimpleNamespace):
    pass


def _base():
    def safe_json_dumps(payload):
        if isinstance(payload, dict) and payload.get("poison"):
            raise TypeError("synthetic serialization failure")
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    return DummyBase(
        EventSender=DummySender,
        safe_json_dumps=safe_json_dumps,
        now_text=lambda: "2026-07-11T15:00:00+09:00",
    )


def test_poison_event_is_dropped_but_following_status_is_sent():
    base = _base()
    install(base)
    sender = base.EventSender()
    sock = DummySocket()

    result = sender._send_batch(
        sock,
        [
            {"type": "collector_status", "poison": True},
            {"type": "collector_status", "status": {"ok": True}},
        ],
    )

    assert result is True
    assert sender.sent_count == 1
    assert sender.serialization_error_count == 1
    assert sender.serialization_drop_count == 1
    assert sender.serialization_last_event_type == "collector_status"
    assert b'"ok":true' in sock.payloads[0]
    stats = sender.stats()
    assert stats["serialization_error_count"] == 1
    assert stats["sender_resilience"] == "collector_sender_fail_open_v1"


def test_socket_failure_requeues_unsent_events_and_returns_false():
    base = _base()
    # DummySender may already be patched by the first test; install is idempotent.
    install(base)
    sender = base.EventSender()
    sock = DummySocket(fail=True)
    events = [
        {"type": "collector_status", "status": {"seq": 1}},
        {"type": "collector_status", "status": {"seq": 2}},
    ]

    assert sender._send_batch(sock, events) is False
    assert sender.requeued == events
    assert "synthetic socket failure" in sender.last_error
