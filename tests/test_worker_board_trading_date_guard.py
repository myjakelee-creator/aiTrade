from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from realtime_v2 import worker_board_trading_date_guard as guard_module


def _portable_payload() -> dict:
    def row(price, rate, value, previous, open_price, high, low):
        ohlc = {
            "open": open_price,
            "high": high,
            "low": low,
            "close": price,
            "date": "20260717",
            "source": "ka10086_AL_exact_date",
            "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
        }
        return {
            "price": price,
            "change_rate": rate,
            "trade_value_eok": value,
            "prev_trade_value_eok": previous,
            "prev_trade_value_date": "20260716",
            "market_scope": "integrated_AL",
            "quality": "EXACT_HISTORICAL_FIELDS",
            "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
            "source_trading_date": "20260717",
            "price_trading_date": "20260717",
            "change_rate_trading_date": "20260717",
            "trade_value_trading_date": "20260717",
            "ohlc_trading_date": "20260717",
            "ohlc": ohlc,
        }

    return {
        "portable_policy_version": guard_module.PORTABLE_POLICY_VERSION,
        "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
        "verified": True,
        "source_trading_date": "20260717",
        "trading_date": "20260717",
        "market_scope": "integrated_AL_regular_fallback",
        "coverage": 1.0,
        "ts": "2026-07-19T08:00:00+09:00",
        "board_values": {
            "000001": row(120, 20.0, 25.0, 10.0, 100, 125, 95),
            "000002": row(200, 0.0, 30.0, 15.0, 210, 220, 190),
        },
    }


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.seed_rank_by_code = {"000001": 1, "000002": 2}
        self.prev_trade_value_by_code = {}
        self.prev_rank_by_code = {}
        self.status = {}
        self.quotes = {
            "000001": {
                "stock_code": "000001",
                "price": 999,
                "change_rate": -99,
                "trade_value_eok": 999,
                "row_source": "seed_universe",
            },
            "000002": {
                "stock_code": "000002",
                "price": 888,
                "change_rate": -88,
                "trade_value_eok": 888,
                "row_source": "seed_universe",
            },
        }

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})


def test_closed_board_uses_one_atomic_exact_date_snapshot(monkeypatch, tmp_path: Path):
    snapshot = tmp_path / "ohlc_snapshot.json"
    snapshot.write_text(json.dumps(_portable_payload()), encoding="utf-8")
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260717", "holiday", False),
    )

    state = _State()
    guard = guard_module.PortableBoardGuard(snapshot)

    assert guard.apply(state) is True
    first = state.quotes["000001"]
    assert first["price"] == 120
    assert first["change_rate"] == 20.0
    assert first["trade_value_eok"] == 25.0
    assert first["prev_trade_value_eok"] == 10.0
    assert first["ohlc"]["date"] == "20260717"
    assert first["price_trading_date"] == "20260717"
    assert first["trade_value_trading_date"] == "20260717"
    assert first["ohlc_trading_date"] == "20260717"
    assert first["row_source"] == "portable_exact_close"
    assert first["portable_parser_version"] == guard_module.PORTABLE_PARSER_VERSION

    assert state.prev_rank_by_code == {"000002": 1, "000001": 2}
    assert state.status["board_display_basis"] == "portable_exact_close"
    assert state.status["board_exact_row_count"] == 2
    assert state.status["board_snapshot_path"] == str(snapshot)
    assert state.status["board_portable_generation"] == 1
    assert (
        state.status["board_portable_parser_version"]
        == guard_module.PORTABLE_PARSER_VERSION
    )

    applied_at = first["portable_board_applied_at"]
    first["price"] = 121
    assert guard.apply(state) is True
    assert first["price"] == 121
    assert first["portable_board_applied_at"] == applied_at
    assert state.status["board_portable_generation"] == 1


def test_closed_board_blocks_old_parser_and_wrong_seed(monkeypatch, tmp_path: Path):
    snapshot = tmp_path / "ohlc_snapshot.json"
    payload = _portable_payload()
    payload["portable_policy_version"] = "portable_closed_board_snapshot_v1"
    payload["portable_parser_version"] = None
    snapshot.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260717", "holiday", False),
    )
    state = _State()
    guard = guard_module.PortableBoardGuard(snapshot)

    assert guard.apply(state) is False
    assert state.status["board_display_basis"] == "blocked_waiting_exact_portable_snapshot"
    assert state.status["board_expected_trading_date"] == "20260717"
    assert state.status["board_missing_row_count"] == 2
    assert state.status["board_portable_generation"] == 1


def test_active_session_keeps_verified_realtime_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260720", "regular", True),
    )
    state = _State()
    guard = guard_module.PortableBoardGuard(tmp_path / "missing.json")

    assert guard.apply(state) is True
    assert state.quotes["000001"]["price"] == 999
    assert state.status["board_display_basis"] == "live_session_passthrough"


def test_install_wraps_init_and_rows_without_network_or_new_loop(
    monkeypatch,
    tmp_path: Path,
):
    snapshot = tmp_path / "ohlc_snapshot.json"
    snapshot.write_text(json.dumps(_portable_payload()), encoding="utf-8")
    guard_class = guard_module.PortableBoardGuard
    monkeypatch.setattr(
        guard_module, "PortableBoardGuard", lambda: guard_class(snapshot)
    )
    monkeypatch.setattr(
        guard_module,
        "board_target_context",
        lambda _now=None: ("20260717", "closed", False),
    )

    class State(_State):
        def rows(self, limit=300):
            return sorted(
                self.quotes.values(),
                key=lambda row: -float(row.get("trade_value_eok") or 0),
            )[:limit]

    base = SimpleNamespace(State=State)
    guard_module.install(base)
    state = State()
    rows = state.rows(300)

    assert [row["stock_code"] for row in rows] == ["000002", "000001"]
    assert isinstance(state.portable_board_guard, guard_class)
    assert getattr(State, "_stockboard_portable_board_guard_installed", False) is True
