from __future__ import annotations

import threading
from collections import deque
from types import SimpleNamespace

from realtime_v2.collector_sender_ordering_patch import install


class Sender:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_trade_by_code = {}
        self.latest_orderbook_by_code = {}
        self.direct_events = deque()

    def stats(self):
        return {}


def make_base():
    published = []

    def publish_collector_status(_sender, _provider, extra=None):
        published.append(dict(extra or {}))

    return SimpleNamespace(
        EventSender=Sender,
        normalize_code=lambda value: str(value or "")[:6] or None,
        publish_collector_status=publish_collector_status,
        now_text=lambda: "2026-07-13T12:00:00",
        published=published,
    )


def trade(code: str, ts: str, buy: int = 0):
    return {
        "type": "trade",
        "stock_code": code,
        "ts": ts,
        "kwargs": {"collector_buy_qty": buy},
    }


def test_direct_events_keep_original_order_when_prepended():
    base = make_base()
    install(base)
    sender = base.EventSender()
    sender.direct_events.append({"type": "collector_status", "collector_status_seq": 3})

    sender._requeue_unsent(
        [
            {"type": "collector_status", "collector_status_seq": 1},
            {"type": "collector_status", "collector_status_seq": 2},
        ]
    )

    assert [item["collector_status_seq"] for item in sender.direct_events] == [1, 2, 3]


def test_older_unsent_trade_does_not_replace_newer_pending_trade():
    base = make_base()
    install(base)
    sender = base.EventSender()
    sender.latest_trade_by_code["005930"] = trade("005930", "12:00:02", buy=20)

    sender._requeue_unsent([trade("005930", "12:00:01", buy=10)])

    kept = sender.latest_trade_by_code["005930"]
    assert kept["ts"] == "12:00:02"
    assert kept["kwargs"]["collector_buy_qty"] == 30
    assert sender.requeue_superseded_trade_count == 1


def test_status_metadata_has_instance_and_monotonic_sequence():
    base = make_base()
    install(base)

    base.publish_collector_status(None, None, {"registered_count": 10})
    base.publish_collector_status(None, None, {"registered_count": 10})

    first, second = base.published
    assert first["collector_instance_id"] == second["collector_instance_id"]
    assert first["collector_pid"] > 0
    assert first["collector_status_seq"] == 1
    assert second["collector_status_seq"] == 2
