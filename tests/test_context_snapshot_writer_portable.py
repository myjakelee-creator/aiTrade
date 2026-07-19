from __future__ import annotations

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
    change_rate: float | None = None,
) -> dict:
    row = {
        "date": date_text,
        "open_pric": open_price,
        "high_pric": high_price,
        "low_pric": low_price,
        "cur_prc": close_price,
        "close_pric": close_price,
        "amt_mn": amount_million,
    }
    if change_rate is not None:
        row["flu_rt"] = change_rate
    return row


def test_portable_snapshot_rebuilds_exact_target_day_without_pc_runtime(
    monkeypatch, tmp_path: Path
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
                change_rate=20.0,
            ),
            _daily_row(
                "20260716",
                open_price=90,
                high_price=105,
                low_price=85,
                close_price=100,
                amount_million=1000,
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
    assert first["ohlc"]["date"] == "20260717"
    assert first["market_scope"] == "integrated_AL"

    second = payload["board_values"]["000002"]
    assert second["change_rate"] == 0.0
    assert second["trade_value_eok"] == 30.0
    assert second["prev_trade_value_eok"] == 15.0


def test_closed_phase_refresh_runs_only_once_in_existing_context_loop(
    monkeypatch, tmp_path: Path
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
        return {
            "portable_policy_version": portable.PORTABLE_POLICY_VERSION,
            "verified": True,
            "source_trading_date": "20260717",
            "trading_date": "20260717",
            "board_value_count": 1,
            "coverage": 1.0,
        }

    monkeypatch.setattr(portable, "fetch_ohlc_bootstrap", fake_fetch)
    monkeypatch.setattr(portable.sf, "write_status", lambda _status: None)

    status = {}
    portable._run_cycle(status)
    portable._run_cycle(status)

    assert calls["fetch"] == 1
    assert portable._snapshot_is_ready("20260717") is True
    assert status["portable_board_refresh_status"] == "exact_ready"
