from __future__ import annotations

import json
import threading
import time
from decimal import Decimal
from pathlib import Path

from realtime_v2.tr_singleflight import TRSingleFlightCoordinator


def test_singleflight_executes_one_physical_fetch_for_concurrent_callers(tmp_path: Path):
    coordinator_a = TRSingleFlightCoordinator(tmp_path)
    coordinator_b = TRSingleFlightCoordinator(tmp_path)
    fetch_count = 0
    fetch_lock = threading.Lock()
    release = threading.Event()
    results = []
    errors = []

    def fetcher():
        nonlocal fetch_count
        with fetch_lock:
            fetch_count += 1
        release.wait(timeout=2.0)
        return {"values": {"000001": 1}}

    def run(coordinator):
        try:
            result = coordinator.execute(
                provider="kiwoom_rest",
                tr_code="ka90004",
                params={"market": "all"},
                trading_date="20260713",
                market_session="regular",
                ttl_sec=60,
                wait_timeout_sec=2,
                poll_sec=0.01,
                fetcher=fetcher,
            )
            results.append(result)
        except Exception as error:  # pragma: no cover - assertion below reports it
            errors.append(error)

    first = threading.Thread(target=run, args=(coordinator_a,))
    second = threading.Thread(target=run, args=(coordinator_b,))
    first.start()
    time.sleep(0.05)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert errors == []
    assert fetch_count == 1
    assert results == [
        {"values": {"000001": 1}},
        {"values": {"000001": 1}},
    ]
    assert coordinator_a.status()["physical_fetch_count"] + coordinator_b.status()["physical_fetch_count"] == 1


def test_normalized_params_share_cache_and_different_params_do_not(tmp_path: Path):
    coordinator = TRSingleFlightCoordinator(tmp_path)
    fetch_count = 0

    def fetcher():
        nonlocal fetch_count
        fetch_count += 1
        return {"fetch_count": fetch_count}

    first = coordinator.execute(
        provider="KIWOOM_REST",
        tr_code="KA10051",
        params={"b": 2, "a": 1},
        trading_date="20260713",
        ttl_sec=60,
        fetcher=fetcher,
    )
    second = coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka10051",
        params={"a": 1, "b": 2},
        trading_date="20260713",
        ttl_sec=60,
        fetcher=fetcher,
    )
    third = coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka10051",
        params={"a": 1, "b": 3},
        trading_date="20260713",
        ttl_sec=60,
        fetcher=fetcher,
    )

    assert first == {"fetch_count": 1}
    assert second == {"fetch_count": 1}
    assert third == {"fetch_count": 2}
    assert coordinator.status()["cache_hit_count"] >= 1


def test_decimal_payload_is_normalized_before_cache_write(tmp_path: Path):
    coordinator = TRSingleFlightCoordinator(tmp_path)

    result = coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka90004_program_net",
        params={"scope": "stockboard_universe"},
        trading_date="20260715",
        market_session="regular_or_latest",
        ttl_sec=60,
        fetcher=lambda: {
            "values": {"000001": Decimal("12.5")},
            "divisor": Decimal("100"),
            "metadata": {
                "request_sleep_seconds": Decimal("0.25"),
                "path": Path("cache/source.json"),
            },
        },
    )

    assert result == {
        "values": {"000001": 12.5},
        "divisor": 100,
        "metadata": {
            "request_sleep_seconds": 0.25,
            "path": "cache/source.json",
        },
    }

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    cached = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert cached["payload"] == result

    cached_again = coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka90004_program_net",
        params={"scope": "stockboard_universe"},
        trading_date="20260715",
        market_session="regular_or_latest",
        ttl_sec=60,
        fetcher=lambda: (_ for _ in ()).throw(AssertionError("cache was not reused")),
    )
    assert cached_again == result


def test_stale_cache_is_returned_when_refresh_fails(tmp_path: Path):
    coordinator = TRSingleFlightCoordinator(tmp_path)
    cached = coordinator.execute(
        provider="yahoo",
        tr_code="bundle",
        params={"symbols": ["NQ=F"]},
        ttl_sec=60,
        fetcher=lambda: {"ok": True},
    )
    assert cached == {"ok": True}

    time.sleep(0.02)
    stale = coordinator.execute(
        provider="yahoo",
        tr_code="bundle",
        params={"symbols": ["NQ=F"]},
        ttl_sec=0,
        stale_if_error=True,
        fetcher=lambda: (_ for _ in ()).throw(RuntimeError("temporary failure")),
    )
    assert stale == {"ok": True}
    assert coordinator.status()["stale_return_count"] == 1
