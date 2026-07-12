from __future__ import annotations

import threading
from types import SimpleNamespace

from realtime_v2.board_platform.fast_path_optimize import install


def _state_class():
    class FakeState:
        def __init__(self):
            self.lock = threading.RLock()
            self.seed_rank_by_code = {"005930": 1, "000660": 2}
            self.quotes = {
                "005930": {"stock_code": "005930", "price": 100},
                "000660": {"stock_code": "000660", "price": 200},
            }
            self.status = {}
            self.original_quote_calls = 0
            self.ohlc_snapshot_mtime = 1
            self.strength_snapshot_mtime = 1
            self.strength_snapshot_by_code = {}

        def _quote(self, code):
            self.original_quote_calls += 1
            return self.quotes.setdefault(code, {"stock_code": code})

        def rows(self, limit=300):
            with self.lock:
                for code in list(self.seed_rank_by_code):
                    self._quote(code)
                return list(self.quotes.values())[:limit]

    return FakeState


def _actual_module():
    def normalize_code(value):
        return str(value).zfill(6)

    def load_ohlc(state, force=False):
        if force:
            state.ohlc_snapshot_mtime += 1

    def load_strength(state, force=False):
        if force:
            state.strength_snapshot_mtime += 1

    def apply_ohlc(state, code, quote):
        quote["ohlc_snapshot_applied_at"] = state.ohlc_snapshot_mtime

    def apply_strength(state, code, quote):
        quote["strength_5m"] = state.strength_snapshot_mtime

    return SimpleNamespace(
        normalize_code=normalize_code,
        _load_ohlc_snapshot_if_needed=load_ohlc,
        _load_strength_snapshot_if_needed=load_strength,
        _apply_ohlc_snapshot_to_quote=apply_ohlc,
        _apply_strength_snapshot_to_quote=apply_strength,
    )


def test_existing_quotes_reuse_original_object_without_reenrichment():
    actual = _actual_module()
    state_class = _state_class()
    base = SimpleNamespace(State=state_class)
    install(actual, base)

    state = state_class()
    rows = state.rows()

    assert len(rows) == 2
    assert state.original_quote_calls == 0

    existing = state._quote("005930")
    assert existing is state.quotes["005930"]
    assert state.original_quote_calls == 0


def test_missing_quote_still_uses_original_initializer():
    actual = _actual_module()
    state_class = _state_class()
    base = SimpleNamespace(State=state_class)
    install(actual, base)

    state = state_class()
    state.seed_rank_by_code["035420"] = 3
    state.rows()

    assert state.original_quote_calls == 1
    assert "035420" in state.quotes


def test_snapshot_file_changes_are_bulk_applied_once():
    actual = _actual_module()
    state_class = _state_class()
    base = SimpleNamespace(State=state_class)
    install(actual, base)

    state = state_class()
    state.strength_snapshot_by_code = {
        "005930": {"strength_5m": 120},
        "000660": {"strength_5m": 130},
    }

    actual._load_ohlc_snapshot_if_needed(state, force=True)
    actual._load_strength_snapshot_if_needed(state, force=True)

    assert state.status["fast_ohlc_bulk_apply_last"] == 2
    assert state.status["fast_strength_bulk_apply_last"] == 2
    assert state.quotes["005930"]["ohlc_snapshot_applied_at"] == 2
    assert state.quotes["000660"]["strength_5m"] == 2


def test_board_platform_installs_optimization_before_profiler():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "realtime_v2"
        / "board_platform"
        / "__init__.py"
    ).read_text(encoding="utf-8")

    assert source.index("install_fast_path_optimize(actual_module, base)") < source.index(
        "install_fast_path_profile(actual_module, base)"
    )
