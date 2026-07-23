from __future__ import annotations

import json
import threading
from pathlib import Path

from realtime_v2 import worker_board_display_continuity_patch as continuity
from realtime_v2 import worker_board_display_continuity_runtime_opt as runtime_opt
from realtime_v2 import worker_board_trading_date_guard as guard_module


def _row(code: str) -> dict:
    return {
        "stock_code": code,
        "price": 120.0,
        "change_rate": 2.0,
        "trade_value_eok": 25.0,
        "prev_trade_value_eok": 10.0,
        "ohlc": {
            "open": 100.0,
            "high": 125.0,
            "low": 95.0,
            "close": 120.0,
            "date": "20260720",
            "source_trading_date": "20260720",
            "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
        },
        "source_trading_date": "20260720",
        "price_trading_date": "20260720",
        "change_rate_trading_date": "20260720",
        "trade_value_trading_date": "20260720",
        "ohlc_trading_date": "20260720",
        "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
    }


def _payload() -> dict:
    return {
        "portable_policy_version": guard_module.PORTABLE_POLICY_VERSION,
        "portable_parser_version": guard_module.PORTABLE_PARSER_VERSION,
        "verified": True,
        "source_trading_date": "20260720",
        "trading_date": "20260720",
        "board_values": {"000001": _row("000001")},
    }


class _Guard:
    def __init__(self, tmp_path: Path, payload: dict):
        self.snapshot_path = tmp_path / "ohlc_snapshot.json"
        self._display_continuity_ready = True
        self._display_last_good_path = tmp_path / "portable_board_display_last_good.json"
        self._display_candidate_path = tmp_path / "ohlc_snapshot_candidate.json"
        self._display_last_good_payload = payload
        self._display_last_fingerprint = continuity._content_fingerprint(payload)
        self._display_generation_by_fingerprint = {}
        self._display_next_generation = 0
        self.load_count = 0
        self.snapshot_path.write_text(json.dumps(payload), encoding="utf-8")

    def _load(self, force=False):
        self.load_count += 1
        return json.loads(self.snapshot_path.read_text(encoding="utf-8"))


class _HoldState:
    def __init__(self):
        self.lock = threading.RLock()
        self.seed_rank_by_code = {"000001": 1, "000002": 2}
        self.prev_trade_value_by_code = {}
        self.prev_rank_by_code = {}
        self._board_display_live_codes_by_date = {"20260721": {"000002"}}
        self.quotes = {
            "000001": {"stock_code": "000001", "price": 999.0},
            "000002": {
                "stock_code": "000002",
                "price": 222.0,
                "received_at": "2026-07-21T08:00:01+09:00",
            },
        }

    def _quote(self, code: str) -> dict:
        return self.quotes.setdefault(code, {"stock_code": code})


def test_active_payload_reads_and_hashes_only_on_first_lookup(tmp_path: Path):
    payload = _payload()
    guard = _Guard(tmp_path, payload)

    first, first_fingerprint, first_generation, first_status = runtime_opt._active_payload(
        continuity,
        guard_module,
        guard,
        "20260720",
    )
    first_reads = guard._display_active_file_read_count
    first_lookups = guard._display_active_lookup_count
    first_loads = guard.load_count

    second, second_fingerprint, second_generation, second_status = runtime_opt._active_payload(
        continuity,
        guard_module,
        guard,
        "20260720",
    )

    assert first_status == "loaded"
    assert second_status == "hit"
    assert first is second
    assert first_fingerprint == second_fingerprint
    assert first_generation == second_generation == 1
    assert first_reads == 3
    assert guard._display_active_file_read_count == first_reads
    assert guard._display_active_lookup_count == first_lookups == 1
    assert guard.load_count == first_loads == 1
    assert guard._display_active_hit_count == 1


def test_active_payload_cache_is_invalidated_only_when_previous_date_changes(tmp_path: Path):
    guard = _Guard(tmp_path, _payload())

    payload, _fingerprint, _generation, status = runtime_opt._active_payload(
        continuity,
        guard_module,
        guard,
        "20260720",
    )
    assert isinstance(payload, dict)
    assert status == "loaded"

    missing, _fingerprint, _generation, next_status = runtime_opt._active_payload(
        continuity,
        guard_module,
        guard,
        "20260721",
    )
    assert missing is None
    assert next_status == "miss"
    assert guard._display_active_payload_date == "20260721"
    assert guard._display_active_lookup_count == 2


def test_regular_session_uses_long_retry_but_premarket_stays_fast():
    assert runtime_opt._retry_sec_for_phase("premarket") == 5.0
    assert runtime_opt._retry_sec_for_phase("opening_call") == 5.0
    assert runtime_opt._retry_sec_for_phase("regular") == 300.0
    assert runtime_opt._retry_sec_for_phase("opening_burst") == 300.0


def test_close_hold_keeps_non_live_stock_and_preserves_first_new_day_live_stock():
    payload = _payload()
    payload["board_values"]["000002"] = {
        **_row("000002"),
        "price": 130.0,
        "trade_value_eok": 20.0,
    }
    state = _HoldState()

    applied, held, live = continuity._apply_payload(
        guard_module,
        state,
        payload,
        source_date="20260720",
        current_date="20260721",
        hold_only=True,
        generation=1,
    )

    assert applied == held == 1
    assert live == 1
    assert state.quotes["000001"]["price"] == 120.0
    assert state.quotes["000001"]["row_source"] == "portable_exact_close"
    assert state.quotes["000001"]["trade_value_eok"] == 25.0
    assert state.quotes["000002"]["price"] == 222.0
    assert state.quotes["000002"]["received_at"] == "2026-07-21T08:00:01+09:00"
