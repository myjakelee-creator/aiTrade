from __future__ import annotations

import ast
import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import realtime_v2.worker_realtime_strength_close_hold_patch as hold_module
from realtime_v2.worker_realtime_strength_close_hold_patch import (
    HOLD_SOURCE,
    LIVE_SOURCE,
    PERSIST_KEYS,
    _display_allowed,
    _entry_valid,
    install,
)

ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "worker_realtime_strength_close_hold_patch.py"
CONFIG_PATH = ROOT / "configs" / "stockboard_live_metrics_rest.json"
PRICE_COLLECTOR_PATH = ROOT / "realtime_v2" / "collector32_large_bidask.py"


class BaseFakeState:
    initial_daily_values = {}

    def __init__(self):
        self.lock = threading.RLock()
        self.status = {}
        self.daily_values_by_code = {
            code: dict(values)
            for code, values in self.initial_daily_values.items()
        }
        self.quotes = {
            "000660": {
                "stock_code": "000660",
                "rank": 1,
            }
        }
        self.daily_dirty = False
        self.persist_count = 0

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})

    def _mark_daily_dirty(self):
        self.daily_dirty = True

    def apply_realtime_strength_ws(self, event):
        code = event["stock_code"]
        value = float(event["execution_strength"])
        target_values = {
            "execution_strength": value,
            "execution_strength_source": LIVE_SOURCE,
            "execution_strength_status": "ok",
            "execution_strength_updated_at": "2026-07-15T19:59:58+09:00",
            "execution_strength_received_at": "2026-07-15T19:59:58+09:00",
            "execution_strength_source_time": event.get(
                "execution_strength_source_time"
            ),
            "execution_strength_exchange": event.get(
                "execution_strength_exchange"
            ),
            "last_valid_execution_strength": value,
            "last_valid_strength_at": "2026-07-15T19:59:58+09:00",
        }
        quote = self._quote(code)
        daily = self.daily_values_by_code.setdefault(code, {})
        quote.update(target_values)
        daily.update(target_values)
        self._mark_daily_dirty()
        return True

    def rows(self, limit=300):
        return [dict(row) for row in self.quotes.values()][:limit]

    def persist_daily_state_if_needed(self, force=False):
        self.persist_count += 1
        return bool(force or self.daily_dirty)


class BaseFakeProgramUpdater:
    def __init__(self, state, *args, **kwargs):
        self.state = state
        self.stopped = False

    def stop(self):
        self.stopped = True


def _config(snapshot_path: Path) -> dict:
    return {
        "realtime_strength_ws": {
            "close_hold": {
                "enabled": True,
                "save_interval_sec": 5,
                "display_until": "next_premarket",
                "display_phases": [
                    "closed",
                    "before_market",
                    "weekend",
                    "holiday",
                ],
                "snapshot_file": str(snapshot_path),
            }
        }
    }


def _install(
    monkeypatch,
    tmp_path,
    *,
    phase="closed",
    initial_daily_values=None,
):
    import realtime_v2.worker_rest_live_metrics_patch as rest_module

    snapshot_path = tmp_path / "strength_hold.json"
    config = _config(snapshot_path)
    monkeypatch.setattr(rest_module, "_read_config", lambda: config)
    monkeypatch.setattr(
        hold_module,
        "market_session_now",
        lambda: SimpleNamespace(phase=phase),
    )
    monkeypatch.setattr(
        hold_module,
        "next_premarket_datetime",
        lambda now=None: datetime(2099, 1, 2, 8, 0, 0),
    )

    class LocalState(BaseFakeState):
        pass

    class LocalProgramUpdater(BaseFakeProgramUpdater):
        pass

    class LocalBase:
        State = LocalState
        ProgramNetUpdater = LocalProgramUpdater
        DAILY_PERSIST_KEYS = ()

    LocalState.initial_daily_values = dict(initial_daily_values or {})
    install(LocalBase)
    return LocalBase, LocalState, snapshot_path


def test_final_fid228_is_saved_displayed_and_restored_after_restart(
    monkeypatch,
    tmp_path,
):
    local_base, local_state, snapshot_path = _install(
        monkeypatch,
        tmp_path,
        phase="closed",
    )
    state = local_state()

    state.apply_realtime_strength_ws(
        {
            "stock_code": "000660",
            "execution_strength": 95.04,
            "execution_strength_source_time": "195958",
            "execution_strength_exchange": "2",
        }
    )
    state.flush_realtime_strength_close_hold(force=True)

    assert snapshot_path.is_file()
    saved = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert saved["values"]["000660"]["execution_strength"] == 95.04

    row = state.rows()[0]
    assert row["execution_strength"] == 95.04
    assert row["execution_strength_source"] == HOLD_SOURCE
    assert row["execution_strength_status"] == "market_closed_final_hold"
    assert row["execution_strength_live"] is False
    assert row["execution_strength_hold_until"] == "2099-01-02T08:00:00"

    restarted = local_state()
    restarted_row = restarted.rows()[0]
    assert restarted_row["execution_strength"] == 95.04
    assert restarted_row["execution_strength_source"] == HOLD_SOURCE
    assert restarted.status["realtime_strength_close_hold_loaded_count"] == 1
    assert set(PERSIST_KEYS).issubset(set(local_base.DAILY_PERSIST_KEYS))


def test_hold_stops_displaying_at_premarket(monkeypatch, tmp_path):
    _local_base, local_state, _snapshot_path = _install(
        monkeypatch,
        tmp_path,
        phase="closed",
    )
    state = local_state()
    state.apply_realtime_strength_ws(
        {
            "stock_code": "000660",
            "execution_strength": 95.04,
            "execution_strength_source_time": "195958",
        }
    )
    state.flush_realtime_strength_close_hold(force=True)

    monkeypatch.setattr(
        hold_module,
        "market_session_now",
        lambda: SimpleNamespace(phase="premarket"),
    )
    row = state.rows()[0]

    assert row["execution_strength_source"] == LIVE_SOURCE
    assert row.get("execution_strength_display_basis") is None
    assert state.status["realtime_strength_close_hold_display_active"] is False
    assert state.status["realtime_strength_close_hold_displayed_rows"] == 0


def test_ka10046_and_zero_values_are_never_promoted_to_final_hold(
    monkeypatch,
    tmp_path,
):
    initial = {
        "000660": {
            "execution_strength": 98.68,
            "execution_strength_source": "ka10046_rest_lowload",
        },
        "005930": {
            "execution_strength": 0,
            "execution_strength_source": LIVE_SOURCE,
        },
    }
    _local_base, local_state, _snapshot_path = _install(
        monkeypatch,
        tmp_path,
        phase="closed",
        initial_daily_values=initial,
    )
    state = local_state()
    row = state.rows()[0]

    assert state.status["realtime_strength_close_hold_loaded_count"] == 0
    assert state.status["realtime_strength_close_hold_valid_count"] == 0
    assert row.get("execution_strength_source") != HOLD_SOURCE


def test_hold_entry_expires_at_boundary_and_phase_policy_is_explicit():
    entry = {
        "source": LIVE_SOURCE,
        "execution_strength": 95.04,
        "expires_at": "2026-07-16T08:00:00",
    }

    assert _entry_valid(entry, datetime(2026, 7, 16, 7, 59, 59)) is True
    assert _entry_valid(entry, datetime(2026, 7, 16, 8, 0, 0)) is False
    config = _config(Path("hold.json"))
    assert _display_allowed(config, "closed") is True
    assert _display_allowed(config, "before_market") is True
    assert _display_allowed(config, "premarket") is False
    assert _display_allowed(config, "regular") is False


def test_close_hold_adds_no_thread_socket_network_or_qax_work():
    source = PATCH_PATH.read_text(encoding="utf-8")
    ast.parse(source)
    for forbidden in (
        "threading.Thread",
        "import socket",
        "from socket",
        "urlopen(",
        "requests.",
        "QAxWidget",
        "SetRealReg",
        "GetCommRealData",
        "dynamicCall",
        "document.",
    ):
        assert forbidden not in source

    collector_source = PRICE_COLLECTOR_PATH.read_text(encoding="utf-8")
    assert '_REALTIME_FIDS = "10;12;20;14"' in collector_source

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    hold = config["realtime_strength_ws"]["close_hold"]
    assert hold["enabled"] is True
    assert hold["display_until"] == "next_premarket"
    assert "premarket" not in hold["display_phases"]
    contract = config["performance_contract"]
    assert contract["realtime_strength_close_hold_threads"] == 0
    assert contract["realtime_strength_close_hold_network_requests"] == 0
