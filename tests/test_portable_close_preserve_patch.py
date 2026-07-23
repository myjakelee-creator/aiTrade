from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2 import portable_close_preserve_patch as patch
from realtime_v2 import worker_board_trading_date_guard as guard_module


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "price": 273000,
                "trade_price": 273000,
                "change_rate": 4.8,
                "trade_value_eok": 77400.0,
                "source_trading_date": "20260723",
                "row_source": "realtime",
            }
        }

    def _quote(self, code):
        return self.quotes.setdefault(code, {})


class _Guard:
    calls = 0

    def apply(self, state, now=None):
        type(self).calls += 1
        state.quotes["005930"].clear()
        return False


def test_existing_close_row_outranks_portable_fallback(monkeypatch):
    _Guard.calls = 0
    monkeypatch.setattr(guard_module, "PortableBoardGuard", _Guard)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260723", "closed", False),
    )
    patch.install(SimpleNamespace())
    state = _State()
    guard = _Guard()

    assert guard.apply(state) is True
    assert _Guard.calls == 0
    assert state.quotes["005930"]["price"] == 273000
    assert state.quotes["005930"]["trade_value_eok"] == 77400.0
    assert state.status["board_display_basis"] == "preserved_live_close_before_portable_fallback"


def test_failed_fallback_restores_usable_rows(monkeypatch):
    _Guard.calls = 0
    monkeypatch.setattr(guard_module, "PortableBoardGuard", _Guard)
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260723", "closed", False),
    )
    patch.install(SimpleNamespace())
    state = _State()
    state.quotes["005930"].pop("source_trading_date")
    guard = _Guard()

    assert guard.apply(state) is True
    assert state.quotes["005930"]["price"] == 273000
    assert state.quotes["005930"]["portable_fallback_preserved"] is True
