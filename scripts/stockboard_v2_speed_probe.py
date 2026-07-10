from __future__ import annotations

import argparse
import csv
import http.client
import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
HOST = "127.0.0.1"
PORT = 8765
PATH = "/api/v2/snapshot?limit=300"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def num(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def int_value(value: Any, default: int = 0) -> int:
    number = num(value)
    return int(number) if number is not None else default


def fetch_snapshot(timeout: float) -> tuple[dict[str, Any] | None, float, str]:
    start = time.perf_counter()
    connection: http.client.HTTPConnection | None = None
    try:
        connection = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
        connection.request("GET", PATH, headers={"Cache-Control": "no-store"})
        response = connection.getresponse()
        body = response.read().decode("utf-8-sig")
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if response.status != 200:
            return None, elapsed_ms, f"http_status_{response.status}"
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return None, elapsed_ms, "payload_not_object"
        return payload, elapsed_ms, ""
    except Exception as error:  # local diagnostic probe must keep running
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return None, elapsed_ms, str(error)
    finally:
        if connection is not None:
            connection.close()


def source_is_realtime(row: dict[str, Any]) -> bool:
    source = str(row.get("row_source") or row.get("source_code") or "")
    return bool(source) and "seed" not in source.lower()


def top20_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    top20 = sorted(rows, key=lambda row: int_value(row.get("rank"), 999999))[:20]
    lag_values: list[float] = []
    stale_count = 0
    realtime_count = 0
    for row in top20:
        lag = num(row.get("fid20_lag_sec"))
        if lag is None:
            lag = num(row.get("price_age_sec"))
        if lag is not None:
            lag_values.append(float(lag))
        age = num(row.get("price_age_sec"))
        if age is not None and age > 3.0:
            stale_count += 1
        if source_is_realtime(row):
            realtime_count += 1
    return {
        "top20_lag_max": max(lag_values) if lag_values else "",
        "top20_stale_count": stale_count,
        "top20_realtime_count": realtime_count,
    }


def percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * ratio)))
    return ordered[index]


def build_row(payload: dict[str, Any] | None, http_ms: float, error: str, previous: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {
            "sample_at": now_text(),
            "ok": 0,
            "error": error,
            "http_ms": round(http_ms, 3),
        }

    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    collector = status.get("collector_status") if isinstance(status.get("collector_status"), dict) else {}
    sender = collector.get("sender_stats") if isinstance(collector.get("sender_stats"), dict) else {}
    raw_rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    rows = [row for row in raw_rows if isinstance(row, dict)]
    top20 = top20_metrics(rows)

    event_count = int_value(status.get("event_count"))
    trade_count = int_value(status.get("trade_count"))
    orderbook_count = int_value(status.get("orderbook_count"))
    prev_event = int_value(previous.get("event_count")) if previous else event_count
    prev_trade = int_value(previous.get("trade_count")) if previous else trade_count
    prev_orderbook = int_value(previous.get("orderbook_count")) if previous else orderbook_count

    return {
        "sample_at": now_text(),
        "ok": 1,
        "error": "",
        "http_ms": round(http_ms, 3),
        "row_count": int_value(payload.get("row_count"), len(rows)),
        "event_count": event_count,
        "event_delta": event_count - prev_event,
        "trade_count": trade_count,
        "trade_delta": trade_count - prev_trade,
        "orderbook_count": orderbook_count,
        "orderbook_delta": orderbook_count - prev_orderbook,
        "collector_connected": sender.get("connected", ""),
        "collector_pending_total": int_value(sender.get("pending_total_count")),
        "collector_sent_per_sec": num(sender.get("sent_per_sec"), 0.0),
        "collector_coalesced_trade": int_value(sender.get("coalesced_trade_overwrite_count")),
        "collector_large_buy_count": int_value(sender.get("large_trade_buy_count")),
        "collector_large_sell_count": int_value(sender.get("large_trade_sell_count")),
        "worker_q": int_value(status.get("event_log_queue_size")),
        "drop": int_value(status.get("dropped_trade_count")),
        "logdrop": int_value(status.get("event_log_dropped_count")),
        "top20_lag_max": top20["top20_lag_max"],
        "top20_stale_count": top20["top20_stale_count"],
        "top20_realtime_count": top20["top20_realtime_count"],
        "rt_rows": sum(1 for row in rows if source_is_realtime(row)),
        "market_phase": status.get("market_phase") or status.get("market_phase_label") or "",
    }


def write_summary(path: Path, rows: list[dict[str, Any]]) -> None:
    ok_rows = [row for row in rows if row.get("ok") == 1]
    http_values = [float(row["http_ms"]) for row in ok_rows if row.get("http_ms") not in (None, "")]
    lag_values = [float(row["top20_lag_max"]) for row in ok_rows if row.get("top20_lag_max") not in (None, "")]
    lines = [
        "StockBoard v2 local speed probe summary",
        f"created_at={now_text()}",
        f"samples={len(rows)}",
        f"ok_samples={len(ok_rows)}",
        f"error_samples={len(rows) - len(ok_rows)}",
    ]
    if http_values:
        lines.extend(
            [
                f"http_ms_avg={statistics.fmean(http_values):.2f}",
                f"http_ms_p95={percentile(http_values, 0.95):.2f}",
                f"http_ms_max={max(http_values):.2f}",
            ]
        )
    if lag_values:
        lines.extend(
            [
                f"top20_lag_max_avg={statistics.fmean(lag_values):.2f}",
                f"top20_lag_max_p95={percentile(lag_values, 0.95):.2f}",
                f"top20_lag_max_max={max(lag_values):.2f}",
            ]
        )
    if ok_rows:
        lines.extend(
            [
                f"collector_pending_total_max={max(int_value(row.get('collector_pending_total')) for row in ok_rows)}",
                f"worker_q_max={max(int_value(row.get('worker_q')) for row in ok_rows)}",
                f"top20_stale_count_max={max(int_value(row.get('top20_stale_count')) for row in ok_rows)}",
                f"rt_rows_last={ok_rows[-1].get('rt_rows')}",
                f"event_delta_total={int_value(ok_rows[-1].get('event_count')) - int_value(ok_rows[0].get('event_count')) if len(ok_rows) > 1 else 0}",
                f"trade_delta_total={int_value(ok_rows[-1].get('trade_count')) - int_value(ok_rows[0].get('trade_count')) if len(ok_rows) > 1 else 0}",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Record local StockBoard v2 speed metrics.")
    parser.add_argument("--seconds", type=float, default=420.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"speed_probe_{stamp}.csv"
    summary_path = OUT_DIR / f"speed_probe_{stamp}_summary.txt"

    fields = [
        "sample_at",
        "ok",
        "error",
        "http_ms",
        "row_count",
        "event_count",
        "event_delta",
        "trade_count",
        "trade_delta",
        "orderbook_count",
        "orderbook_delta",
        "collector_connected",
        "collector_pending_total",
        "collector_sent_per_sec",
        "collector_coalesced_trade",
        "collector_large_buy_count",
        "collector_large_sell_count",
        "worker_q",
        "drop",
        "logdrop",
        "top20_lag_max",
        "top20_stale_count",
        "top20_realtime_count",
        "rt_rows",
        "market_phase",
    ]

    print(f"SPEED_PROBE=http://{HOST}:{PORT}{PATH}")
    print(f"CSV={csv_path}")
    print(f"SUMMARY={summary_path}")
    print(f"SECONDS={args.seconds} INTERVAL={args.interval}")

    rows: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    deadline = time.monotonic() + max(0.0, args.seconds)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        sample_index = 0
        while sample_index == 0 or time.monotonic() < deadline:
            loop_start = time.monotonic()
            payload, http_ms, error = fetch_snapshot(args.timeout)
            row = build_row(payload, http_ms, error, previous)
            if row.get("ok") == 1:
                previous = row
            for field in fields:
                row.setdefault(field, "")
            writer.writerow(row)
            handle.flush()
            rows.append(row)
            sample_index += 1
            print(
                "{sample_at} ok={ok} http={http_ms}ms rows={row_count} "
                "ev+{event_delta} tr+{trade_delta} q={collector_pending_total}/{worker_q} "
                "lag={top20_lag_max} stale={top20_stale_count} rt={rt_rows}".format(**row)
            )
            sleep_for = max(0.0, args.interval - (time.monotonic() - loop_start))
            if sleep_for:
                time.sleep(sleep_for)

    write_summary(summary_path, rows)
    print(f"DONE CSV={csv_path}")
    print(f"DONE SUMMARY={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
