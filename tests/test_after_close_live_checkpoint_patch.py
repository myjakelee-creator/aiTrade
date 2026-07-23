from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import after_close_live_hold_patch as patch


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.status = {"trade_count": 10}
        self.quotes = {
            "005930": {
                "stock_code": "005930",
                "price": 271000,
                "trade_price": 271000,
                "change_rate": 4.8,
                "trade_value_eok": 77809.0,
                "received_at": "2026-07-23T20:00:03.000+09:00",
                "price_trading_date": "20260723",
                "trade_value_trading_date": "20260723",
            }
        }

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})


def _base(tmp_path: Path):
    return SimpleNamespace(RUNTIME_DIR=tmp_path)


def test_after_close_checkpoint_round_trip(tmp_path: Path):
    base = _base(tmp_path)
    state = _State()

    assert patch._save_checkpoint_if_due(base, state, "20260723", "closed") is True
    checkpoint = json.loads((tmp_path / patch.CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
    assert checkpoint["trading_date"] == "20260723"
    assert checkpoint["row_count"] == 1
    assert checkpoint["rows"]["005930"]["price"] == 271000

    restarted = _State()
    restarted.status["trade_count"] = 0
    restarted.quotes = {}
    restored = patch._restore_checkpoint(base, restarted, "20260723", "closed")

    assert restored == 1
    assert restarted.quotes["005930"]["price"] == 271000
    assert restarted.quotes["005930"]["row_source"] == "after_close_live_checkpoint"
    assert restarted.status["board_display_basis"] == "after_close_live_checkpoint"


def test_checkpoint_does_not_overwrite_existing_live_values(tmp_path: Path):
    base = _base(tmp_path)
    state = _State()
    assert patch._save_checkpoint_if_due(base, state, "20260723", "closed") is True

    current = _State()
    current.quotes["005930"]["price"] = 272000
    restored = patch._restore_checkpoint(base, current, "20260723", "closed")

    assert restored == 0
    assert current.quotes["005930"]["price"] == 272000


def test_wrong_date_checkpoint_is_rejected(tmp_path: Path):
    base = _base(tmp_path)
    state = _State()
    assert patch._save_checkpoint_if_due(base, state, "20260723", "closed") is True

    restarted = _State()
    restarted.status["trade_count"] = 0
    restarted.quotes = {}
    assert patch._restore_checkpoint(base, restarted, "20260724", "closed") == 0
    assert restarted.quotes == {}


def test_regular_phase_never_writes_checkpoint(tmp_path: Path):
    base = _base(tmp_path)
    state = _State()
    assert patch._save_checkpoint_if_due(base, state, "20260723", "regular") is False
    assert not (tmp_path / patch.CHECKPOINT_FILENAME).exists()
