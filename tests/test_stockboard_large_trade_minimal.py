from __future__ import annotations

import ast
import threading
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.collector_large_trade_patch import install


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "collector_large_trade_patch.py"


class FakeSender:
    def __init__(self, *_args, flush_ms: int = 50, **_kwargs):
        self.lock = threading.Lock()
        self.flush_sec = flush_ms / 1000.0
        self.latest_trade_by_code = {}
        self.latest_orderbook_by_code = {}
        self.trade_flow_by_code = {}
        self.direct_events = []

    def publish_trade(self, event):
        code = normalize_code(event.get("stock_code"))
        with self.lock:
            self.latest_trade_by_code[code] = event

    def _attach_trade_flow(self, _code, event, _flow):
        return event

    def stats(self):
        return {}


def normalize_code(value):
    text = str(value or "").replace("_AL", "")
    return text if len(text) == 6 and text.isdigit() else ""


def event_trade_qty(event):
    values = event.get("kwargs") or {}
    raw = values.get("raw") or {}
    value = raw.get("trade_qty_raw") or values.get("trade_qty")
    return int(value) if value not in (None, "") else None


def build_base():
    return SimpleNamespace(
        EventSender=FakeSender,
        normalize_code=normalize_code,
        _event_trade_qty=event_trade_qty,
    )


def trade(qty: int, price: int = 100_000):
    return {
        "type": "trade",
        "stock_code": "005930",
        "kwargs": {
            "price": str(price),
            "trade_qty": str(qty),
            "raw": {
                "price_raw": str(price),
                "trade_qty_raw": str(qty),
            },
        },
    }


def test_large_trade_patch_is_pure_python_and_has_no_qax_or_tr_path():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    assert "dynamicCall" not in source
    assert "QAxWidget" not in source
    assert "KiwoomOpenApiRealtimeProvider" not in source
    assert "SetRealReg" not in source
    assert "QTimer" not in source


def test_every_qualifying_signed_tick_survives_latest_only_coalescing():
    base = build_base()
    install(base)
    sender = base.EventSender(flush_ms=50)

    sender.publish_trade(trade(600))    # 60,000,000 KRW buy
    sender.publish_trade(trade(-700))   # 70,000,000 KRW sell
    sender.publish_trade(trade(100))    # 10,000,000 KRW, below threshold

    events = sender._drain()
    assert len(events) == 1
    kwargs = events[0]["kwargs"]
    assert kwargs["collector_large_trade_buy_count_delta"] == 1
    assert kwargs["collector_large_trade_sell_count_delta"] == 1
    assert kwargs["collector_large_trade_buy_sum_eok_delta"] == 0.6
    assert kwargs["collector_large_trade_sell_sum_eok_delta"] == 0.7
    assert kwargs["large_trade_threshold_krw"] == 50_000_000

    stats = sender.stats()
    assert stats["large_trade_aggregate_enabled"] is True
    assert stats["large_trade_buy_count"] == 1
    assert stats["large_trade_sell_count"] == 1
    assert stats["pending_large_trade_code_count"] == 0
