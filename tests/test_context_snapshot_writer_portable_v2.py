from __future__ import annotations

from pathlib import Path

from realtime_v2 import context_snapshot_writer_portable_v2 as portable_v2

portable = portable_v2.portable


def _daily_row(
    date_text: str,
    *,
    open_price: int,
    high_price: int,
    low_price: int,
    close_price: int,
    current_price: int | None,
    amount_million: int,
    official_rate: float | None,
) -> dict:
    row = {
        "date": date_text,
        "open_pric": open_price,
        "high_pric": high_price,
        "low_pric": low_price,
        "close_pric": close_price,
        "amt_mn": amount_million,
    }
    if current_price is not None:
        row["cur_prc"] = current_price
    if official_rate is not None:
        row["flu_rt"] = official_rate
    return row


def test_exact_row_uses_close_pric_and_official_flu_rt(monkeypatch, tmp_path: Path):
    codes = tmp_path / "codes.txt"
    codes.write_text("000660\n", encoding="utf-8")

    rows = [
        _daily_row(
            "20260716",
            open_price=1980000,
            high_price=2025000,
            low_price=1800000,
            close_price=-1830000,
            current_price=2022000,
            amount_million=23153400,
            official_rate=-12.10,
        ),
        _daily_row(
            "20260715",
            open_price=2010000,
            high_price=2050000,
            low_price=1980000,
            close_price=-2022000,
            current_price=None,
            amount_million=12000000,
            official_rate=None,
        ),
    ]

    monkeypatch.setattr(portable, "issue_access_token", lambda: "token")
    monkeypatch.setattr(
        portable,
        "_request_daily_rows",
        lambda _token, _code, _target, _sleep: (
            rows,
            "ka10086_AL_exact_date",
            None,
        ),
    )

    payload = portable_v2.build_portable_snapshot(
        codes,
        limit=300,
        sleep_sec=0,
        target_date="20260716",
        market_phase="weekend",
    )

    assert payload["portable_parser_version"] == "exact_daily_row_fields_v2"
    assert payload["verified"] is True

    row = payload["board_values"]["000660"]
    assert row["price"] == 1830000
    assert row["change_rate"] == -12.10
    assert row["change_rate_source"] == "exact_row_flu_rt"
    assert row["ohlc"]["close"] == 1830000
    assert row["portable_parser_version"] == "exact_daily_row_fields_v2"

    diagnostic = payload["diagnostic_samples"][0]
    assert diagnostic["raw_cur_prc"] == 2022000
    assert diagnostic["raw_close_pric"] == -1830000
    assert diagnostic["raw_flu_rt"] == -12.10
    assert diagnostic["selected_close"] == 1830000
    assert diagnostic["selected_change_rate"] == -12.10
    assert diagnostic["change_rate_source"] == "exact_row_flu_rt"
    assert round(diagnostic["computed_change_rate"], 4) == -9.4955
    assert round(diagnostic["change_rate_delta"], 4) == -2.6045


def test_missing_official_rate_falls_back_to_historical_close_calculation():
    exact = _daily_row(
        "20260716",
        open_price=100,
        high_price=125,
        low_price=95,
        close_price=120,
        current_price=999,
        amount_million=2500,
        official_rate=None,
    )
    previous = _daily_row(
        "20260715",
        open_price=90,
        high_price=105,
        low_price=85,
        close_price=100,
        current_price=None,
        amount_million=1000,
        official_rate=None,
    )

    selected = portable._historical_change_rate(exact, previous)

    assert selected == 20.0
    assert exact["_portable_change_rate_source"] == (
        "computed_from_historical_closes"
    )
    assert exact["_portable_computed_change_rate"] == 20.0


def test_v2_candidate_validation_rejects_v1_parser(monkeypatch, tmp_path: Path):
    codes = tmp_path / "codes.txt"
    codes.write_text("000001\n", encoding="utf-8")
    rows = [
        _daily_row(
            "20260716",
            open_price=100,
            high_price=125,
            low_price=95,
            close_price=120,
            current_price=777,
            amount_million=2500,
            official_rate=20.0,
        ),
        _daily_row(
            "20260715",
            open_price=90,
            high_price=105,
            low_price=85,
            close_price=100,
            current_price=None,
            amount_million=1000,
            official_rate=None,
        ),
    ]
    monkeypatch.setattr(portable, "issue_access_token", lambda: "token")
    monkeypatch.setattr(
        portable,
        "_request_daily_rows",
        lambda *_args: (rows, "ka10086_regular_exact_date", None),
    )
    payload = portable_v2.build_portable_snapshot(
        codes, 300, 0, "20260716", "weekend"
    )

    valid, reason = portable_v2.validate_portable_candidate(
        payload, "20260716"
    )
    assert valid is True
    assert reason == "ok"

    payload["portable_parser_version"] = "exact_daily_row_fields_v1"
    valid, reason = portable_v2.validate_portable_candidate(
        payload, "20260716"
    )
    assert valid is False
    assert "parser version mismatch" in reason


def test_launcher_uses_v2_context_entrypoint():
    script = (
        Path("scripts/start_context_singleflight.ps1")
        .read_text(encoding="utf-8-sig")
    )
    assert (
        '$ModuleName = "realtime_v2.context_snapshot_writer_portable_v2"'
        in script
    )
