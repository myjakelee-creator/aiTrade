from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import realtime_v2.build_universe as build


def cached_payload(count: int = 3) -> dict:
    items = []
    for index in range(count):
        code = f"{index + 1:06d}"
        items.append(
            {
                "stock_code": code,
                "stock_name": f"종목{index + 1}",
                "seed_rank": index + 1,
                "original_rank": index + 1,
                "seed_trade_value_eok": 100 - index,
                "seed_change_rate": 1.0,
                "seed_price": 1000 + index,
            }
        )
    return {
        "schema_version": 1,
        "source": "ka10032_seed_universe_filtered_by_tradable_master",
        "universe_source_status": "live",
        "universe_fallback_active": False,
        "built_at": "2026-07-10T15:30:00",
        "trading_date": "20260710",
        "rank_basis": "today",
        "limit": count,
        "count": count,
        "page_counts": [count],
        "filtered_out_not_tradable": 0,
        "previous_trade_value_attached_count": 0,
        "previous_trade_value_cached_count": 0,
        "previous_trade_value_direct_regular_count": 0,
        "previous_trade_value_missing_count": count,
        "items": items,
    }


def test_seed_fetch_retries_then_returns_live_rows(monkeypatch):
    calls = []

    monkeypatch.setattr(build, "issue_access_token", lambda: "token")

    def fake_fetch(_token, rank_basis):
        calls.append(rank_basis)
        if len(calls) < 3:
            raise RuntimeError("temporary ka10032 1631")
        return ([{"stock_code": "005930"}], [1])

    monkeypatch.setattr(build, "fetch_trade_value_top100", fake_fetch)
    monkeypatch.setattr(build.time, "sleep", lambda _seconds: None)

    token, rows, page_counts, attempt_count = build._fetch_seed_rows_with_retry(
        "today",
        retry_count=3,
        retry_delay_sec=0,
    )

    assert token == "token"
    assert rows == [{"stock_code": "005930"}]
    assert page_counts == [1]
    assert attempt_count == 3
    assert calls == ["today", "today", "today"]


def test_cached_fallback_preserves_original_basis_and_marks_stale(tmp_path, monkeypatch):
    output = tmp_path / "universe.json"
    output.write_text(json.dumps(cached_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STOCKBOARD_V2_UNIVERSE_FALLBACK_MIN_COUNT", "2")

    payload = build._cached_universe_fallback(
        output,
        limit=3,
        rank_basis="today",
        build_error=RuntimeError("ka10032 error 1631"),
    )

    assert payload["count"] == 3
    assert payload["universe_source_status"] == "stale_fallback"
    assert payload["universe_fallback_active"] is True
    assert payload["source"] == "cached_universe_fallback_after_build_error"
    assert payload["universe_original_source"] == "ka10032_seed_universe_filtered_by_tradable_master"
    assert payload["universe_original_built_at"] == "2026-07-10T15:30:00"
    assert payload["universe_original_trading_date"] == "20260710"
    assert "1631" in payload["universe_fallback_reason"]


def test_repeated_fallback_keeps_first_live_metadata(tmp_path, monkeypatch):
    output = tmp_path / "universe.json"
    first = cached_payload()
    output.write_text(json.dumps(first, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STOCKBOARD_V2_UNIVERSE_FALLBACK_MIN_COUNT", "2")

    fallback_one = build._cached_universe_fallback(
        output,
        limit=3,
        rank_basis="today",
        build_error=RuntimeError("first failure"),
    )
    output.write_text(json.dumps(fallback_one, ensure_ascii=False), encoding="utf-8")

    fallback_two = build._cached_universe_fallback(
        output,
        limit=3,
        rank_basis="today",
        build_error=RuntimeError("second failure"),
    )

    assert fallback_two["universe_original_source"] == first["source"]
    assert fallback_two["universe_original_built_at"] == first["built_at"]
    assert fallback_two["universe_original_trading_date"] == first["trading_date"]
    assert "second failure" in fallback_two["universe_fallback_reason"]


def test_main_continues_with_cache_and_repairs_codes_file(tmp_path, monkeypatch, capsys):
    output = tmp_path / "universe.json"
    output.write_text(json.dumps(cached_payload(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STOCKBOARD_V2_UNIVERSE_FALLBACK_MIN_COUNT", "2")
    monkeypatch.setattr(build, "build_universe", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ka10032 1631")))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_universe.py",
            "--limit",
            "3",
            "--output",
            str(output),
            "--seed-retry-count",
            "1",
            "--seed-retry-delay-sec",
            "0",
        ],
    )

    assert build.main() == 0

    saved = json.loads(output.read_text(encoding="utf-8"))
    codes = output.with_name("codes.txt").read_text(encoding="utf-8").splitlines()
    captured = capsys.readouterr()

    assert saved["universe_fallback_active"] is True
    assert saved["universe_source_status"] == "stale_fallback"
    assert codes == ["000001", "000002", "000003"]
    assert "UNIVERSE_STARTUP_CONTINUED_WITH_CACHE=True" in captured.out
    assert "UNIVERSE_SOURCE_STATUS=stale_fallback" in captured.out


def test_invalid_or_too_small_cache_still_fails(tmp_path, monkeypatch):
    output = tmp_path / "universe.json"
    output.write_text(json.dumps(cached_payload(count=1), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STOCKBOARD_V2_UNIVERSE_FALLBACK_MIN_COUNT", "2")

    with pytest.raises(RuntimeError, match="minimum required is 2"):
        build._cached_universe_fallback(
            output,
            limit=3,
            rank_basis="today",
            build_error=RuntimeError("ka10032 1631"),
        )


def test_missing_cache_still_fails(tmp_path):
    with pytest.raises(RuntimeError, match="cached universe file not found"):
        build._cached_universe_fallback(
            Path(tmp_path / "missing.json"),
            limit=300,
            rank_basis="today",
            build_error=RuntimeError("ka10032 1631"),
        )
