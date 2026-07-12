from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from realtime_v2.after_close_recovery_state import (
    AfterCloseRecoveryStateService,
    load_state,
    recovery_entry,
)


def test_recovery_entry_keeps_only_recovery_fields():
    row = {
        "stock_code": "005930",
        "price": 1000,
        "trade_value_1m_eok": 12.0,
        "minute_recovery_trading_date": "20260710",
        "source_metadata": {"trade_value_1m_eok": {"value": 12.0}},
    }
    entry = recovery_entry(row)
    assert "stock_code" not in entry
    assert "price" not in entry
    assert entry["trade_value_1m_eok"] == 12.0
    assert entry["minute_recovery_trading_date"] == "20260710"


def test_state_file_restores_before_next_premarket(tmp_path):
    path = tmp_path / "after_close_recovery_state.json"
    payload = {
        "schema_version": 1,
        "source": "after_close_recovery_state_v2",
        "target_trading_date": "20260710",
        "saved_at": "2026-07-12T10:00:00",
        "valid_until": "2026-07-13T08:00:00",
        "entries": {
            "005930": {
                "trade_value_1m_eok": 12.0,
                "minute_recovery_trading_date": "20260710",
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    restored = load_state(path, datetime(2026, 7, 12, 18, 0))
    assert restored["005930"]["trade_value_1m_eok"] == 12.0
    assert path.exists()


def test_state_file_expires_at_premarket(tmp_path):
    path = tmp_path / "after_close_recovery_state.json"
    payload = {
        "schema_version": 1,
        "valid_until": "2026-07-13T08:00:00",
        "entries": {"005930": {"trade_value_1m_eok": 12.0}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_state(path, datetime(2026, 7, 13, 8, 0)) == {}
    assert not path.exists()


def test_state_service_batches_and_atomically_saves(tmp_path):
    state = SimpleNamespace(
        lock=threading.RLock(),
        quotes={
            "005930": {
                "stock_code": "005930",
                "trade_value_1m_eok": 12.0,
                "minute_recovery_trading_date": "20260710",
                "source_metadata": {
                    "trade_value_1m_eok": {
                        "value": 12.0,
                        "source": "CLOSE_SAMPLER_EXACT",
                    }
                },
            }
        },
        status={},
    )
    service = AfterCloseRecoveryStateService(
        state,
        path=tmp_path / "state.json",
        now_provider=lambda: datetime(2026, 7, 10, 21, 0),
    )
    service.mark("005930")
    assert service.save(force=True) is True

    payload = json.loads(service.path.read_text(encoding="utf-8"))
    assert payload["entry_count"] == 1
    assert payload["entries"]["005930"]["trade_value_1m_eok"] == 12.0
    assert payload["target_trading_date"] == "20260710"
    assert payload["valid_until"].endswith("08:00:00")


def test_board_platform_installs_recovery_state_last():
    root = Path(__file__).resolve().parents[1]
    platform = (root / "realtime_v2" / "board_platform" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "install_recovery_state(base)" in platform
    assert platform.index("install_theme_recovery(base)") < platform.index(
        "install_recovery_state(base)"
    )
