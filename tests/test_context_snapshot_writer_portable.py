from __future__ import annotations

import json
from pathlib import Path

from realtime_v2 import context_snapshot_writer_portable as portable


def _daily_row(
    date_text: str,
    *,
    open_price: int,
    high_price: int,
    low_price: int,
    close_price: int,
    amount_million: int,
    current_price: int | None = None,
    direct_rate: float | None = None,
) -> dict:
    row = {
        "date": date_text,
        "open_pric": open_price,
        "high_pric": high_price,
        "low_pric": low_price,
        "cur_prc": close_price if current_price is None else current_price,
        "close_pric": close_price,
        "amt_mn": amount_million,
    }
    if direct_rate is not None:
        row["flu_rt"] = direct_rate
    return row


def _valid_payload() -> dict:
    ohlc = {
        "open": 100,
        "high": 125,
        "low": 95,
        "close": 120,
        "date": "20260717",
        "source_trading_date": "20260717",
        "portable_parser_version": portable.PORTABLE_PARSER_VERSION,
    }
    return {
        "schema_version": 3,
        "portable_policy_version": portable.PORTABLE_POLICY_VERSION,
        "portable_parser_version": portable.PORTABLE_PARSER_VERSION,
        "verified": True,
        "source_trading_date": "20260717",
        "trading_date": "20260717",
        "requested_count": 1,
        "coverage": 1.0,
        "board_value_count": 1,
        "board_values": {
            "000001": {
                "stock_code": "000001",
                "price": 120,
                "change_rate": 20.0,
                "trade_value_eok": 25.0,
                "ohlc": ohlc,
                "source_trading_date": "20260717",
                "price_trading_date": "20260717",
                "change_rate_trading_date": "20260717",
                "trade_value_trading_date": "20260717",
                "ohlc_trading_date": "20260717",
                "portable_parser_version": portable.PORTABLE_PARSER_VERSION,
            }
        },
    }


def test_portable_snapshot_uses_historical_close_not_query_time_alias(
    monkeypatch,
    tmp_path: Path,
):
    codes = tmp_path / "codes.txt"
    codes.write_text("000001\n000002\n", encoding="utf-8")

    rows_by_code = {
        "000001": [
            _daily_row(
                "20260717",
                open_price=100,
                high_price=125,
                low_price=95,
                close_price=120,
                amount_million=2500,
                current_price=999,
                direct_rate=777.0,
            ),
            _daily_row(
                "20260716",
                open_price=90,
                high_price=105,
                low_price=85,
                close_price=100,
                amount_million=1000,
                current_price=888,
            ),
        ],
        "000002": [
            _daily_row(
                "20260717",
                open_price=210,
                high_price=220,
                low_price=190,
                close_price=200,
                amount_million=3000,
                current_price=777,
                direct_rate=-55.0,
            ),
            _daily_row(
                "20260716",
                open_price=190,
                high_price=210,
                low_price=180,
                close_price=200,
                amount_million=1500,
            ),
        ],
    }

    monkeypatch.setattr(portable, "issue_access_token", lambda: "token")

    def fake_request(_token, code, target_date, _sleep_sec):
        assert target_date == "20260717"
        return rows_by_code[code], "ka10086_AL_exact_date", None

    monkeypatch.setattr(portable, "_request_daily_rows", fake_request)

    payload = portable.build_portable_snapshot(
        codes,
        limit=300,
        sleep_sec=0,
        target_date="20260717",
        market_phase="holiday",
    )

    assert payload["portable_policy_version"] == portable.PORTABLE_POLICY_VERSION
    assert payload["portable_parser_version"] == portable.PORTABLE_PARSER_VERSION
    assert payload["source_trading_date"] == "20260717"
    assert payload["verified"] is True
    assert payload["coverage"] == 1.0
    assert payload["board_value_count"] == 2

    first = payload["board_values"]["000001"]
    assert first["price"] == 120
    assert first["change_rate"] == 20.0
    assert first["trade_value_eok"] == 25.0
    assert first["prev_trade_value_eok"] == 10.0
    assert first["prev_trade_value_date"] == "20260716"
    assert first["ohlc"]["close"] == 120
    assert first["market_scope"] == "integrated_AL"
    assert first["quality"] == "EXACT_HISTORICAL_FIELDS"

    sample = payload["diagnostic_samples"][0]
    assert sample["raw_cur_prc"] == 999
    assert sample["raw_close_pric"] == 120
    assert sample["raw_flu_rt"] == 777.0
    assert sample["selected_close"] == 120
    assert sample["computed_change_rate"] == 20.0

    second = payload["board_values"]["000002"]
    assert second["price"] == 200
    assert second["change_rate"] == 0.0
    assert second["trade_value_eok"] == 30.0
    assert second["prev_trade_value_eok"] == 15.0


def test_al_row_without_historical_close_falls_back_to_regular(monkeypatch):
    invalid_al = [
        {
            "date": "20260717",
            "open_pric": 100,
            "high_pric": 130,
            "low_pric": 90,
            "cur_prc": 999,
            "amt_mn": 2500,
        },
        _daily_row(
            "20260716",
            open_price=90,
            high_price=105,
            low_price=85,
            close_price=100,
            amount_million=1000,
        ),
    ]
    valid_regular = [
        _daily_row(
            "20260717",
            open_price=100,
            high_price=125,
            low_price=95,
            close_price=120,
            amount_million=2500,
        ),
        _daily_row(
            "20260716",
            open_price=90,
            high_price=105,
            low_price=85,
            close_price=100,
            amount_million=1000,
        ),
    ]

    calls = []

    def fake_post(_path, body, _headers):
        calls.append(body["stk_cd"])
        rows = invalid_al if body["stk_cd"].endswith("_AL") else valid_regular
        return {"daly_stkpc": rows}

    monkeypatch.setattr(portable, "_post_json", fake_post)
    rows, source, error = portable._request_daily_rows(
        "token",
        "000001",
        "20260717",
        0,
    )

    assert error is None
    assert rows == valid_regular
    assert source == "ka10086_regular_exact_date"
    assert calls == ["000001_AL", "000001"]


def test_candidate_validation_rejects_old_parser_and_invalid_ohlc():
    payload = _valid_payload()
    valid, reason = portable.validate_portable_candidate(payload, "20260717")
    assert valid is True
    assert reason == "ok"

    old = dict(payload)
    old["portable_policy_version"] = "portable_closed_board_snapshot_v1"
    valid, reason = portable.validate_portable_candidate(old, "20260717")
    assert valid is False
    assert "policy version" in reason

    invalid = json.loads(json.dumps(payload))
    invalid["board_values"]["000001"]["ohlc"]["close"] = 130
    valid, reason = portable.validate_portable_candidate(invalid, "20260717")
    assert valid is False
    assert "OHLC" in reason or "price/OHLC" in reason


def test_closed_phase_refresh_runs_only_once_in_existing_context_loop(
    monkeypatch,
    tmp_path: Path,
):
    output = tmp_path / "ohlc_snapshot.json"
    codes = tmp_path / "codes.txt"
    codes.write_text("000001\n", encoding="utf-8")

    monkeypatch.setattr(portable.sf.base, "OHLC_SNAPSHOT_FILE", output)
    monkeypatch.setattr(
        portable.sf, "market_supply_target_context", lambda: ("20260717", "closed")
    )
    monkeypatch.setattr(portable, "_original_run_cycle", lambda _status: None)
    monkeypatch.setattr(portable, "_last_bootstrap_args", (codes, 300, 0.0))
    portable._attempted_refresh_keys.clear()

    calls = {"fetch": 0}

    def fake_fetch(_path, _limit, _sleep):
        calls["fetch"] += 1
        return _valid_payload()

    monkeypatch.setattr(portable, "fetch_ohlc_bootstrap", fake_fetch)
    monkeypatch.setattr(portable.sf, "write_status", lambda _status: None)

    status = {}
    portable._run_cycle(status)
    portable._run_cycle(status)

    assert calls["fetch"] == 1
    assert portable._snapshot_is_ready("20260717") is True
    assert status["portable_board_refresh_status"] == "exact_ready"


def test_active_phase_delegates_to_proven_intraday_ohlc_bootstrap(
    monkeypatch,
    tmp_path: Path,
):
    codes = tmp_path / "codes.txt"
    codes.write_text("000001\n", encoding="utf-8")
    monkeypatch.setattr(
        portable.sf,
        "market_supply_target_context",
        lambda: ("20260720", "regular"),
    )
    calls = []

    def original(path, limit, sleep_sec):
        calls.append((Path(path), limit, sleep_sec))
        return {"source": "proven_intraday_bootstrap", "values": {}}

    monkeypatch.setattr(portable, "_original_active_fetch_ohlc_bootstrap", original)
    payload = portable.fetch_ohlc_bootstrap(codes, 300, 0.12)

    assert payload["source"] == "proven_intraday_bootstrap"
    assert calls == [(codes, 300, 0.12)]
