from __future__ import annotations

import json
from pathlib import Path

from realtime_v2 import context_snapshot_retry_patch as retry
from realtime_v2 import context_snapshot_writer_portable_v2 as portable_v2

portable = portable_v2.portable


def _existing_row(code: str) -> dict:
    return {
        "stock_code": code,
        "price": 120.0,
        "change_rate": 20.0,
        "trade_value_eok": 25.0,
        "prev_trade_value_eok": 10.0,
        "prev_trade_value_date": "20260716",
        "ohlc": {
            "open": 100.0,
            "high": 125.0,
            "low": 95.0,
            "close": 120.0,
            "date": "20260717",
            "source_trading_date": "20260717",
            "source": "ka10086_regular_exact_date",
            "portable_parser_version": portable_v2.PORTABLE_PARSER_VERSION,
        },
        "source_trading_date": "20260717",
        "price_trading_date": "20260717",
        "change_rate_trading_date": "20260717",
        "trade_value_trading_date": "20260717",
        "ohlc_trading_date": "20260717",
        "market_scope": "regular_KRX",
        "source": "ka10086_regular_exact_date",
        "quality": "EXACT_HISTORICAL_FIELDS",
        "portable_parser_version": portable_v2.PORTABLE_PARSER_VERSION,
    }


def _daily_rows() -> list[dict]:
    return [
        {
            "date": "20260717",
            "open_pric": 190,
            "high_pric": 220,
            "low_pric": 185,
            "close_pric": 200,
            "flu_rt": 5.0,
            "amt_mn": 3000,
        },
        {
            "date": "20260716",
            "open_pric": 180,
            "high_pric": 195,
            "low_pric": 175,
            "close_pric": 190,
            "amt_mn": 1500,
        },
    ]


def test_incremental_retry_reuses_partial_candidate_and_queries_only_missing(monkeypatch, tmp_path: Path):
    portable_v2._install()
    codes = tmp_path / "codes.txt"
    codes.write_text("000001\n000002\n", encoding="utf-8")
    candidate = tmp_path / "ohlc_snapshot_candidate.json"
    prior = retry._payload(
        portable,
        portable_v2.PORTABLE_PARSER_VERSION,
        ["000001", "000002"],
        {"000001": _existing_row("000001")},
        "20260717",
        "before_market",
    )
    candidate.write_text(json.dumps(prior), encoding="utf-8")

    monkeypatch.setattr(portable, "_candidate_path", lambda: candidate)
    monkeypatch.setattr(portable, "issue_access_token", lambda: "token")
    calls: list[str] = []

    def request(_token, code, _target, _sleep):
        calls.append(code)
        return _daily_rows(), "ka10086_regular_exact_date", None

    monkeypatch.setattr(portable, "_request_daily_rows", request)
    monkeypatch.setattr(portable.sf, "write_status", lambda _status: None)
    monkeypatch.setattr(
        portable.sf,
        "_atomic_write",
        lambda path, payload: Path(path).write_text(json.dumps(payload), encoding="utf-8"),
    )

    status: dict = {}
    payload = retry._build(
        portable,
        portable_v2.PORTABLE_PARSER_VERSION,
        codes,
        300,
        0,
        "20260717",
        "before_market",
        status,
        2,
    )

    assert calls == ["000002"]
    assert set(payload["board_values"]) == {"000001", "000002"}
    assert payload["verified"] is True
    assert status["portable_board_completed_count"] == 2
    assert status["portable_board_pending_count"] == 0


def test_retry_backoff_is_bounded_and_status_is_explicit(monkeypatch):
    monkeypatch.setattr(retry, "RETRY_BASE_SEC", 60.0)
    monkeypatch.setattr(retry, "RETRY_MAX_SEC", 300.0)
    assert [retry._delay(value) for value in (1, 2, 3, 4, 5)] == [60.0, 120.0, 240.0, 300.0, 300.0]

    monkeypatch.setattr(portable.sf, "write_status", lambda _status: None)
    retry._retry_state.clear()
    status: dict = {}
    payload = retry._payload(
        portable,
        portable_v2.PORTABLE_PARSER_VERSION,
        ["000001", "000002"],
        {"000001": _existing_row("000001")},
        "20260717",
        "before_market",
    )
    key = ("20260717", "before_market")
    retry._wait(portable, status, payload, key, 2, "temporary 429")

    assert status["portable_board_refresh_status"] == "retry_wait"
    assert status["portable_board_retry_attempt"] == 2
    assert status["portable_board_last_error"] == "temporary 429"
    assert status["portable_board_completed_count"] == 1
    assert status["portable_board_requested_count"] == 2
    assert status["portable_board_next_retry_at"]
    assert retry._retry_state[key]["next_retry_mono"] > 0
