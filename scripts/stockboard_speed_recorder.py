"""Non-intrusive StockBoard speed recorder.

This recorder never starts, stops, or imports the StockBoard server. It only
polls already-running HTTP endpoints and appends JSONL samples. It is safe to
run during market open because failures are logged as samples instead of
raising and affecting the UI process.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now(KST).isoformat(timespec="milliseconds")


def fetch_json(url: str, timeout: float) -> tuple[Any, float, str | None]:
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload, (time.perf_counter() - started) * 1000.0, None
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as error:
        return None, (time.perf_counter() - started) * 1000.0, str(error)


def count_rows(payload: Any) -> int | None:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return len(rows)
        quotes = payload.get("quotes")
        if isinstance(quotes, dict):
            return len(quotes)
    return None


def build_sample(base_url: str, timeout: float) -> dict[str, Any]:
    endpoints = {
        "health": "/api/health",
        "provider": "/api/realtime_provider_status",
        "top100": "/api/top100",
        "hot_patch": "/api/hot_realtime_patch",
        "realtime_patch": "/api/realtime_patch",
    }
    sample: dict[str, Any] = {"ts": now_text(), "base_url": base_url, "endpoints": {}}
    for name, path in endpoints.items():
        payload, latency_ms, error = fetch_json(f"{base_url}{path}?ts={int(time.time() * 1000)}", timeout)
        endpoint_sample = {
            "latency_ms": round(latency_ms, 3),
            "ok": error is None,
            "error": error,
            "row_count": count_rows(payload),
        }
        if isinstance(payload, dict):
            endpoint_sample.update(
                {
                    "running": payload.get("running"),
                    "login_state": payload.get("login_state"),
                    "registered_count": payload.get("registered_count"),
                    "realdata_received_count": payload.get("realdata_received_count"),
                    "trade_seen_codes_count": payload.get("trade_seen_codes_count"),
                    "orderbook_seen_codes_count": payload.get("orderbook_seen_codes_count"),
                    "price_sequence": payload.get("price_sequence"),
                    "sequence": payload.get("sequence"),
                    "mode": payload.get("mode"),
                    "lane": payload.get("lane"),
                    "skipped": payload.get("skipped"),
                    "skip_reason": payload.get("skip_reason"),
                }
            )
        sample["endpoints"][name] = endpoint_sample
    return sample


def main() -> int:
    parser = argparse.ArgumentParser(description="Record StockBoard endpoint speed without touching the server")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=1.2)
    parser.add_argument("--duration", type=float, default=0.0, help="seconds; 0 means run until Ctrl+C")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    date_text = datetime.now(KST).strftime("%Y%m%d")
    output = Path(args.output) if args.output else ROOT / "data" / "runtime" / "speed_records" / f"stockboard_speed_{date_text}.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    interval = max(0.2, float(args.interval or 1.0))
    timeout = max(0.2, float(args.timeout or 1.2))
    deadline = time.monotonic() + args.duration if args.duration and args.duration > 0 else None

    print(f"StockBoard speed recorder started: {output}", flush=True)
    print("This recorder only polls existing HTTP endpoints. It does not touch UI/server processes.", flush=True)

    try:
        while True:
            started = time.monotonic()
            sample = build_sample(args.base_url.rstrip("/"), timeout)
            with output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(sample, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
            provider = sample["endpoints"].get("provider", {})
            top100 = sample["endpoints"].get("top100", {})
            print(
                f"{sample['ts']} provider_ok={provider.get('ok')} top100_ok={top100.get('ok')} "
                f"top100_ms={top100.get('latency_ms')} rows={top100.get('row_count')} "
                f"realdata={provider.get('realdata_received_count')}",
                flush=True,
            )
            if deadline is not None and time.monotonic() >= deadline:
                return 0
            sleep_for = interval - (time.monotonic() - started)
            if sleep_for > 0:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
