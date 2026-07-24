from __future__ import annotations

"""Low-load Monday opening probe for the frozen StockBoard price baseline.

Operator-only diagnostic. It does not add QAx, FID, REST/TR background work,
WebSocket subscriptions, production timers, or production state mutations.
It polls only ``/api/v2/snapshot?limit=1`` at a conservative interval and writes
CSV/JSON evidence for 08:55~09:10 opening validation.
"""

import argparse
import csv
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
DEFAULT_URL = "http://127.0.0.1:8765/api/v2/snapshot?limit=1"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _find(payload: Any, names: tuple[str, ...]) -> Any:
    if isinstance(payload, dict):
        for name in names:
            if name in payload and payload[name] not in (None, ""):
                return payload[name]
        for value in payload.values():
            found = _find(value, names)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find(value, names)
            if found not in (None, ""):
                return found
    return None


def _fetch(url: str, timeout_sec: float) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=timeout_sec) as response:
        payload = json.loads(response.read().decode("utf-8"))
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if not isinstance(payload, dict):
        raise RuntimeError("snapshot response is not an object")
    return payload, elapsed_ms


def _delta(first: dict[str, Any], last: dict[str, Any], key: str) -> float | None:
    a = _number(first.get(key))
    b = _number(last.get(key))
    return None if a is None or b is None else b - a


def main() -> int:
    parser = argparse.ArgumentParser(description="Low-load StockBoard Monday opening probe")
    parser.add_argument("--duration-sec", type=float, default=900.0)
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--url", default=DEFAULT_URL)
    args = parser.parse_args()

    duration_sec = max(10.0, float(args.duration_sec))
    interval_sec = max(1.0, float(args.interval_sec))
    timeout_sec = max(1.0, float(args.timeout_sec))

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RUNTIME_DIR / f"monday_open_probe_{stamp}.csv"
    json_path = RUNTIME_DIR / f"monday_open_probe_{stamp}.json"

    print("=== StockBoard Monday opening probe ===")
    print(f"started_at   : {_now()}")
    print(f"duration_sec : {duration_sec:.1f}")
    print(f"interval_sec : {interval_sec:.1f}")
    print(f"url          : {args.url}")
    print(f"csv_report   : {csv_path}")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    deadline = time.monotonic() + duration_sec

    fieldnames = [
        "captured_at",
        "http_ms",
        "event_count",
        "trade_count",
        "drop_count",
        "logdrop_count",
        "collector_q",
        "worker_q",
        "recv_per_sec",
        "trade_per_sec",
        "stream_ms",
        "render_ms",
        "top20_lag_sec",
        "stale_count",
        "market_phase",
    ]

    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        while time.monotonic() < deadline:
            loop_started = time.monotonic()
            try:
                payload, http_ms = _fetch(args.url, timeout_sec)
                status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
                row = {
                    "captured_at": _now(),
                    "http_ms": round(http_ms, 3),
                    "event_count": _find(status, ("event_count", "events")),
                    "trade_count": _find(status, ("trade_count", "trades")),
                    "drop_count": _find(status, ("dropped_trade_count", "drop", "dropped_count")),
                    "logdrop_count": _find(status, ("logdrop", "log_drop_count")),
                    "collector_q": _find(status, ("collector_q", "collector_queue", "pending_trade_count")),
                    "worker_q": _find(status, ("worker_q", "worker_queue")),
                    "recv_per_sec": _find(status, ("recv_per_sec", "recv_s", "realdata_per_sec")),
                    "trade_per_sec": _find(status, ("trade_per_sec", "trade_s")),
                    "stream_ms": _find(status, ("stream_ms", "price_stream_ms", "price_patch_ms")),
                    "render_ms": _find(status, ("render_ms",)),
                    "top20_lag_sec": _find(status, ("top20_trade_time_lag_sec", "top20_lag_sec")),
                    "stale_count": _find(status, ("stale_count", "recent_trade_missing_count")),
                    "market_phase": _find(status, ("market_phase", "market_phase_label", "session")),
                }
                rows.append(row)
                writer.writerow(row)
                handle.flush()
                print(
                    f"{row['captured_at']} http={row['http_ms']}ms "
                    f"events={row['event_count']} trades={row['trade_count']} "
                    f"drop={row['drop_count']} cq={row['collector_q']} wq={row['worker_q']}"
                )
            except Exception as exc:  # operator diagnostic must continue sampling
                message = f"{_now()} {type(exc).__name__}: {exc}"
                errors.append(message)
                print(f"WARNING {message}")

            sleep_for = interval_sec - (time.monotonic() - loop_started)
            if sleep_for > 0:
                time.sleep(sleep_for)

    http_values = [value for row in rows if (value := _number(row.get("http_ms"))) is not None]
    summary = {
        "version_name": "SBV2-20260727.M1-MONDAY-OPEN-PRICE-BASE",
        "source_commit": "d8e9af1afc09969e66a7a06d2647d69c2d2f020a",
        "finished_at": _now(),
        "duration_sec": duration_sec,
        "interval_sec": interval_sec,
        "sample_count": len(rows),
        "error_count": len(errors),
        "http_ms_average": round(statistics.fmean(http_values), 3) if http_values else None,
        "http_ms_median": round(statistics.median(http_values), 3) if http_values else None,
        "http_ms_max": round(max(http_values), 3) if http_values else None,
        "event_increase": _delta(rows[0], rows[-1], "event_count") if len(rows) >= 2 else None,
        "trade_increase": _delta(rows[0], rows[-1], "trade_count") if len(rows) >= 2 else None,
        "drop_increase": _delta(rows[0], rows[-1], "drop_count") if len(rows) >= 2 else None,
        "logdrop_increase": _delta(rows[0], rows[-1], "logdrop_count") if len(rows) >= 2 else None,
        "collector_q_max": max(
            (value for row in rows if (value := _number(row.get("collector_q"))) is not None),
            default=None,
        ),
        "worker_q_max": max(
            (value for row in rows if (value := _number(row.get("worker_q"))) is not None),
            default=None,
        ),
        "errors": errors,
        "operator_only_no_production_path_change": True,
    }
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Summary ===")
    for key, value in summary.items():
        if key != "errors":
            print(f"{key:36}: {value}")
    print(f"CSV_REPORT={csv_path}")
    print(f"JSON_REPORT={json_path}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
