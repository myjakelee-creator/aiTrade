from __future__ import annotations

import argparse
import hashlib
import importlib
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from realtime_v2.common import trading_date_text  # noqa: E402
from realtime_v2.tr_singleflight import get_shared_tr_coordinator  # noqa: E402


base = importlib.import_module("realtime_v2.context_snapshot_writer_base")
coordinator = get_shared_tr_coordinator()

_original_fetch_yahoo_snapshot = base.fetch_yahoo_snapshot
_original_fetch_live_market_supply_snapshot = base.fetch_live_market_supply_snapshot
_original_fetch_ohlc_bootstrap = base.fetch_ohlc_bootstrap
_original_write_status = base.write_status
_original_atomic_write = base._atomic_write

_CONTEXT_RUNTIME_VERSION = "singleflight_explicit_loop_v3"


def _file_fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "missing"


def _inject_context_status(payload):
    status = dict(payload or {})
    status["context_owner"] = "tr_singleflight"
    status["context_entrypoint"] = "realtime_v2.context_snapshot_writer_singleflight"
    status["context_process_pid"] = os.getpid()
    status["context_runtime_version"] = _CONTEXT_RUNTIME_VERSION
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


def _run_cycle(status: dict) -> None:
    try:
        try:
            us_payload = fetch_yahoo_snapshot()
            _atomic_write(base.US_MARKET_FILE, us_payload)
            status["us_market_status"] = "ok"
            status.pop("us_market_error", None)
        except Exception as error:
            status["us_market_status"] = "error"
            status["us_market_error"] = str(error)

        try:
            try:
                market_payload = fetch_live_market_supply_snapshot()
                status["market_supply_status"] = "live_ok"
                status["market_supply_source"] = market_payload.get("source")
                status.pop("market_supply_live_error", None)
                status.pop("market_supply_source_file", None)
            except Exception as live_error:
                market_payload = base.copy_latest_market_supply_snapshot()
                market_payload["live_error"] = str(live_error)
                status["market_supply_status"] = "fallback_file"
                status["market_supply_live_error"] = str(live_error)
                status["market_supply_source_file"] = market_payload.get("source_file")
            _atomic_write(base.MARKET_SUPPLY_FILE, market_payload)
        except Exception as error:
            status["market_supply_status"] = "error"
            status["market_supply_error"] = str(error)

        write_status(status)
    except Exception as error:
        write_status({"status": "loop_error", "error": str(error)})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="StockBoard v2 shared single-flight context snapshot writer"
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=float(os.getenv("STOCKBOARD_V2_CONTEXT_INTERVAL_SEC", "30")),
    )
    parser.add_argument(
        "--ohlc-bootstrap",
        action="store_true",
        default=os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_BOOTSTRAP", "1") == "1",
    )
    parser.add_argument(
        "--ohlc-limit",
        type=int,
        default=int(os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_LIMIT", "300")),
    )
    parser.add_argument(
        "--ohlc-sleep-sec",
        type=float,
        default=float(os.getenv("STOCKBOARD_V2_CONTEXT_OHLC_SLEEP_SEC", "0.12")),
    )
    parser.add_argument("--codes-file", default=str(base.RUNTIME_DIR / "codes.txt"))
    args = parser.parse_args()

    base.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    status = {
        "started_at": base.now_text(),
        "pid": os.getpid(),
        "interval_sec": args.interval_sec,
    }
    write_status(status)

    if args.ohlc_bootstrap:
        try:
            ohlc_payload = fetch_ohlc_bootstrap(
                Path(args.codes_file), args.ohlc_limit, args.ohlc_sleep_sec
            )
            _atomic_write(base.OHLC_SNAPSHOT_FILE, ohlc_payload)
            status["ohlc_count"] = ohlc_payload.get("count")
            status["ohlc_error_count"] = ohlc_payload.get("error_count")
            status.pop("ohlc_error", None)
        except Exception as error:
            status["ohlc_error"] = str(error)
        write_status(status)

    while True:
        _run_cycle(status)
        time.sleep(max(15.0, float(args.interval_sec or 30.0)))


base._atomic_write = _atomic_write
base.fetch_yahoo_snapshot = fetch_yahoo_snapshot
base.fetch_live_market_supply_snapshot = fetch_live_market_supply_snapshot
base.fetch_ohlc_bootstrap = fetch_ohlc_bootstrap
base.write_status = write_status


if __name__ == "__main__":
    raise SystemExit(main())
