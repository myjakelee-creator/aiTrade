from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path

from realtime_v2.common import trading_date_text
from realtime_v2.tr_singleflight import get_shared_tr_coordinator


base = importlib.import_module("realtime_v2.context_snapshot_writer_base")
coordinator = get_shared_tr_coordinator()

_original_fetch_yahoo_snapshot = base.fetch_yahoo_snapshot
_original_fetch_live_market_supply_snapshot = base.fetch_live_market_supply_snapshot
_original_fetch_ohlc_bootstrap = base.fetch_ohlc_bootstrap
_original_write_status = base.write_status
_original_atomic_write = base._atomic_write


def _file_fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _inject_context_status(payload):
    status = dict(payload or {})
    status["context_owner"] = "tr_singleflight"
    status["context_entrypoint"] = "realtime_v2.context_snapshot_writer"
    status["context_process_pid"] = os.getpid()
    status["tr_singleflight"] = coordinator.status()
    return status


def _atomic_write(path: Path, payload):
    target = Path(path)
    if target == Path(base.STATUS_FILE):
        payload = _inject_context_status(payload)
    return _original_atomic_write(target, payload)


def fetch_yahoo_snapshot(timeout: float = 5.0):
    return coordinator.execute(
        provider="yahoo_chart",
        tr_code="us_market_bundle",
        params={"symbols": sorted(base.YAHOO_SYMBOLS.values())},
        trading_date=trading_date_text(),
        market_session="global_latest",
        ttl_sec=15.0,
        wait_timeout_sec=max(10.0, float(timeout) * 3.0),
        fetcher=lambda: _original_fetch_yahoo_snapshot(timeout=timeout),
    )


def fetch_live_market_supply_snapshot():
    trade_date = trading_date_text()
    return coordinator.execute(
        provider="kiwoom_rest",
        tr_code="market_supply_bundle",
        params={"markets": ["KOSPI", "KOSDAQ"]},
        trading_date=trade_date,
        market_session="regular_or_latest",
        ttl_sec=20.0,
        wait_timeout_sec=60.0,
        fetcher=_original_fetch_live_market_supply_snapshot,
    )


def fetch_ohlc_bootstrap(
    codes_file: Path,
    limit: int = 300,
    sleep_sec: float = 0.12,
):
    path = Path(codes_file)
    trade_date = trading_date_text()
    return coordinator.execute(
        provider="kiwoom_rest",
        tr_code="ka10086_ohlc_bootstrap_bundle",
        params={
            "codes_fingerprint": _file_fingerprint(path),
            "limit": int(limit or 300),
            "suffix_policy": "AL_first_regular_fallback",
        },
        trading_date=trade_date,
        market_session="daily_bootstrap",
        ttl_sec=6 * 60 * 60,
        wait_timeout_sec=max(
            120.0,
            float(limit or 300) * max(0.05, float(sleep_sec)) * 3.0,
        ),
        lease_timeout_sec=30 * 60,
        fetcher=lambda: _original_fetch_ohlc_bootstrap(path, limit, sleep_sec),
    )


def write_status(status):
    return _original_write_status(_inject_context_status(status))


# Patch both the named status helper and the underlying status-file write path.
# The second guard makes owner/single-flight telemetry survive every base writer
# path, including startup, loop errors, and future direct STATUS_FILE writes.
base._atomic_write = _atomic_write
base.fetch_yahoo_snapshot = fetch_yahoo_snapshot
base.fetch_live_market_supply_snapshot = fetch_live_market_supply_snapshot
base.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
base.write_status = write_status

main = base.main


if __name__ == "__main__":
    raise SystemExit(main())
