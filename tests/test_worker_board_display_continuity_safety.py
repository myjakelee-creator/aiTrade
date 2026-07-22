from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2 import worker_board_display_continuity_patch as continuity
from realtime_v2 import worker_board_display_continuity_safety as safety
from realtime_v2 import worker_board_trading_date_guard as guard_module


class _State:
    def __init__(self):
        self.lock = threading.RLock()
        self.seed_rank_by_code = {"000001": 1, "000002": 2, "000003": 3}
        self.status = {}
        self.quotes = {
            code: {
                "stock_code": code,
                "stock_name": code,
                "price": 900 + index,
                "change_rate": -9.0,
                "trade_value_eok": 99.0,
                "row_source": "seed_universe",
                "ohlc": {"open": 1, "high": 2, "low": 1, "close": 2},
            }
            for index, code in enumerate(self.seed_rank_by_code, start=1)
        }
        self._board_display_live_codes_by_date = {"20260720": {"000002"}}

    def _quote(self, code):
        return self.quotes.setdefault(code, {"stock_code": code})


def _exact_row() -> dict:
    return {
        "stock_code": "000001",
        "price": 120.0,
        "change_rate": 2.0,
        "trade_value_eok": 25.0,
        "ohlc": {
            "open": 100.0,
            "high": 125.0,
            "low": 95.0,
            "close": 120.0,
            "date": "20260717",
            "source_trading_date": "20260717",
            "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
        },
        "source_trading_date": "20260717",
        "price_trading_date": "20260717",
        "change_rate_trading_date": "20260717",
        "trade_value_trading_date": "20260717",
        "ohlc_trading_date": "20260717",
        "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
    }


def _payload() -> dict:
    return {
        "portable_policy_version": guard_module.PORTABLE_POLICY_VERSION,
        "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
        "source_trading_date": "20260717",
        "board_values": {"000001": _exact_row()},
    }


def _pure_cache_row(code: str, price: float = 120.0) -> dict:
    return {
        "stock_code": code,
        "price": price,
        "change_rate": 2.0,
        "trade_value_eok": 25.0,
        "row_source": "portable_exact_close",
        "source_code": "portable_exact_close",
        "source_trading_date": "20260717",
        "price_trading_date": "20260717",
        "change_rate_trading_date": "20260717",
        "trade_value_trading_date": "20260717",
        "ohlc_trading_date": "20260717",
    }


def _live_cache_row(code: str) -> dict:
    return {
        "stock_code": code,
        "price": 130.0,
        "change_rate": 3.0,
        "trade_value_eok": 30.0,
        "row_source": "realtime",
        "source_trading_date": "20260720",
        "price_trading_date": "20260720",
        "change_rate_trading_date": "20260720",
        "trade_value_trading_date": "20260720",
        "ohlc_trading_date": "20260720",
        "received_at": "2026-07-20T08:18:45+09:00",
    }


def test_active_hold_preserves_exact_and_current_live_but_clears_missing_seed():
    state = _State()

    suppressed = safety._suppress_unverified_seed_rows(
        continuity,
        guard_module,
        state,
        _payload(),
        source_date="20260717",
        current_date="20260720",
        hold_only=True,
    )

    assert suppressed == 1
    assert state.quotes["000001"]["price"] == 901
    assert state.quotes["000002"]["price"] == 902
    assert "price" not in state.quotes["000003"]
    assert "change_rate" not in state.quotes["000003"]
    assert "trade_value_eok" not in state.quotes["000003"]
    assert "ohlc" not in state.quotes["000003"]
    assert state.quotes["000003"]["portable_board_missing"] is True
    assert state.status["board_display_seed_suppressed_count"] == 1
    assert state.status["board_display_continuity_safety_version"] == safety.PATCH_VERSION


def test_closed_partial_candidate_clears_every_non_exact_seed_row():
    state = _State()

    suppressed = safety._suppress_unverified_seed_rows(
        continuity,
        guard_module,
        state,
        _payload(),
        source_date="20260717",
        current_date=None,
        hold_only=False,
    )

    assert suppressed == 2
    assert state.quotes["000001"]["price"] == 901
    assert "price" not in state.quotes["000002"]
    assert "price" not in state.quotes["000003"]


def test_pure_verified_cache_rejects_live_or_mixed_rows():
    pure = [_pure_cache_row("000001"), _pure_cache_row("000002", 200.0)]
    mixed = [pure[0], _live_cache_row("000002")]

    assert safety._pure_verified_close_rows(pure, "20260717") is True
    assert safety._pure_verified_close_rows(mixed, "20260717") is False
    assert safety._pure_verified_close_rows(pure, "20260720") is False


def test_generation_rebuild_serves_separate_pure_cache_not_mixed_rows(monkeypatch):
    monkeypatch.setattr(continuity, "_display_continuity_rlock_installed", True, raising=False)

    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}
            self.mode = "pure"

        def snapshot(self, limit=300):
            if self.mode == "pure":
                return {
                    "status": {"board_source_trading_date": "20260717"},
                    "rows": [_pure_cache_row("000001"), _pure_cache_row("000002", 200.0)],
                    "row_count": 2,
                }
            return {
                "status": {
                    "board_source_trading_date": "20260717",
                    "portable_board_cache_sync_status": "serving_previous_verified_during_rebuild",
                },
                "rows": [_pure_cache_row("000001"), _live_cache_row("000002")],
                "row_count": 2,
            }

    base = SimpleNamespace(State=State)
    safety.install(base)
    state = State()

    initial = state.snapshot(100)
    assert initial["row_count"] == 2
    assert state.status["board_display_verified_cache_status"] == "stored_pure_exact_close"

    state.mode = "mixed"
    fallback = state.snapshot(100)

    assert fallback["row_count"] == 2
    assert [row["stock_code"] for row in fallback["rows"]] == ["000001", "000002"]
    assert all(row["row_source"] == "portable_exact_close" for row in fallback["rows"])
    assert all(row["source_trading_date"] == "20260717" for row in fallback["rows"])
    assert fallback["status"]["portable_board_cache_sync_status"] == (
        "serving_separate_verified_close_cache"
    )


def test_mixed_fallback_is_blocked_when_no_pure_cache_exists(monkeypatch):
    monkeypatch.setattr(continuity, "_display_continuity_rlock_installed", True, raising=False)

    class State:
        def __init__(self):
            self.lock = threading.RLock()
            self.status = {}

        def snapshot(self, limit=300):
            return {
                "status": {
                    "board_source_trading_date": "20260717",
                    "portable_board_cache_sync_status": "serving_previous_verified_during_rebuild",
                },
                "rows": [_pure_cache_row("000001"), _live_cache_row("000002")],
                "row_count": 2,
            }

    base = SimpleNamespace(State=State)
    safety.install(base)
    payload = State().snapshot(100)

    assert payload["rows"] == []
    assert payload["row_count"] == 0
    assert payload["status"]["portable_board_cache_sync_status"] == (
        "blocked_no_pure_verified_close_cache"
    )


def test_install_marks_state_contract_without_new_owner_or_loop(monkeypatch):
    original = continuity._apply_payload
    monkeypatch.setattr(continuity, "_display_continuity_rlock_installed", False, raising=False)

    class State:
        pass

    base = SimpleNamespace(State=State)
    safety.install(base)

    assert continuity._apply_payload is not original
    assert continuity._display_continuity_rlock_installed is True
    assert State._stockboard_display_continuity_rlock_installed is True
    assert State._stockboard_display_continuity_rlock_version == safety.PATCH_VERSION
