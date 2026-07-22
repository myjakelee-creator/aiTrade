from __future__ import annotations

"""Trace the existing StockBoard price path for a short operator-requested window.

The tool is never imported by the live Worker or Collector. It adds no QAx owner,
FID, Kiwoom REST call, WebSocket, recurring task, or production timer. It only:

- tails the Worker JSONL event log that already exists,
- opens one existing StockBoard SSE stream,
- reads the existing snapshot once before and once after the trace.
"""

import argparse
import csv
import json
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8765/api/v2/snapshot?limit=300"
DEFAULT_CODES = ("000660", "005930", "009150", "402340", "277810")
DEFAULT_DURATION_SEC = 15.0
DEFAULT_SSE_INTERVAL_MS = 100


def _code(value: Any) -> str:
    text = "".join(str(value or "").split()).upper().split("_", 1)[0]
    if text.startswith("A"):
        text = text[1:]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else ""


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"Cache-Control": "no-cache"})
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("StockBoard snapshot response must be an object")
    return payload


def _stream_url(snapshot_url: str, interval_ms: int) -> str:
    parsed = urlparse(snapshot_url)
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/api/v2/stream",
            "",
            urlencode(
                {
                    "limit": 300,
                    "interval_ms": max(50, min(2000, int(interval_ms))),
                    "ts": int(time.time() * 1000),
                }
            ),
            "",
        )
    )


def _merged_event_values(event: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("values", "kwargs"):
        value = event.get(key)
        if isinstance(value, dict):
            result.update(value)
    return result


def _trade_event_sample(event: dict[str, Any], observed_at: str) -> dict[str, Any] | None:
    if event.get("type") != "trade":
        return None
    values = _merged_event_values(event)
    raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
    code = _code(
        event.get("stock_code")
        or event.get("received_code")
        or values.get("stock_code")
        or values.get("normalized_code")
        or values.get("received_code")
    )
    if not code:
        return None
    observed_ts = _timestamp(observed_at)
    source_ts = _timestamp(event.get("ts"))
    log_delay_ms = (
        round(max(0.0, observed_ts - source_ts) * 1000.0, 3)
        if observed_ts is not None and source_ts is not None
        else None
    )
    return {
        "observed_at": observed_at,
        "event_ts": event.get("ts"),
        "event_log_observe_delay_ms": log_delay_ms,
        "stock_code": code,
        "received_code": event.get("received_code") or values.get("received_code"),
        "source_code": values.get("source_code")
        or values.get("registered_code")
        or event.get("received_code"),
        "price": _number(
            raw.get("price_raw")
            or values.get("price")
            or values.get("trade_price")
            or values.get("realtime_price")
        ),
        "change_rate": _number(
            raw.get("change_rate_raw")
            or values.get("change_rate")
            or values.get("realtime_change_rate")
        ),
        "trade_time": raw.get("trade_time_raw")
        or values.get("fid20_trade_time")
        or values.get("trade_time"),
        "cumulative_value_raw": raw.get("cumulative_value_raw")
        or values.get("cumulative_value"),
        "trade_value_eok": _number(values.get("trade_value_eok")),
    }


def _row_sample(row: dict[str, Any], payload: dict[str, Any], observed_at: str) -> dict[str, Any]:
    observed_ts = _timestamp(observed_at)
    row_received_at = (
        row.get("received_at")
        or row.get("price_received_at")
        or row.get("trade_received_at")
    )
    source_ts = _timestamp(row_received_at)
    transport_ms = (
        round(max(0.0, observed_ts - source_ts) * 1000.0, 3)
        if observed_ts is not None and source_ts is not None
        else None
    )
    payload_ts = payload.get("ts")
    payload_source_ts = _timestamp(payload_ts)
    payload_lag_ms = (
        round(max(0.0, observed_ts - payload_source_ts) * 1000.0, 3)
        if observed_ts is not None and payload_source_ts is not None
        else None
    )
    return {
        "observed_at": observed_at,
        "payload_ts": payload_ts,
        "payload_lag_ms": payload_lag_ms,
        "stock_code": _code(row.get("stock_code")),
        "price": _number(row.get("price") or row.get("trade_price")),
        "change_rate": _number(row.get("change_rate")),
        "trade_value_eok": _number(row.get("trade_value_eok")),
        "trade_time": row.get("trade_time"),
        "row_received_at": row_received_at,
        "row_transport_ms": transport_ms,
        "row_source": row.get("row_source"),
        "source_code": row.get("source_code") or row.get("registered_code"),
        "last_dropped_trade_reason": row.get("last_dropped_trade_reason"),
    }


def _tail_event_log(
    path: Path,
    codes: set[str],
    stop_event: threading.Event,
    output: list[dict[str, Any]],
) -> None:
    deadline_wait = time.monotonic() + 5.0
    while not path.is_file() and not stop_event.is_set() and time.monotonic() < deadline_wait:
        time.sleep(0.05)
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(0, 2)
            while not stop_event.is_set():
                line = handle.readline()
                if not line:
                    time.sleep(0.02)
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                sample = _trade_event_sample(event, _now_iso())
                if sample and sample["stock_code"] in codes:
                    output.append(sample)
    except OSError:
        return


def _consume_sse(
    url: str,
    codes: set[str],
    stop_event: threading.Event,
    output: list[dict[str, Any]],
    snapshot_lags: list[float],
) -> None:
    request = Request(url, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
    try:
        with urlopen(request, timeout=5) as response:
            data_lines: list[str] = []
            while not stop_event.is_set():
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if line or not data_lines:
                    continue
                try:
                    payload = json.loads("\n".join(data_lines))
                except json.JSONDecodeError:
                    data_lines = []
                    continue
                data_lines = []
                if not isinstance(payload, dict):
                    continue
                observed_at = _now_iso()
                rows = payload.get("rows")
                if not isinstance(rows, list):
                    continue
                row_by_code = {
                    _code(row.get("stock_code")): row
                    for row in rows
                    if isinstance(row, dict) and _code(row.get("stock_code"))
                }
                observed_ts = _timestamp(observed_at)
                payload_ts = _timestamp(payload.get("ts"))
                if observed_ts is not None and payload_ts is not None:
                    snapshot_lags.append(max(0.0, observed_ts - payload_ts) * 1000.0)
                for code in codes:
                    row = row_by_code.get(code)
                    if isinstance(row, dict):
                        output.append(_row_sample(row, payload, observed_at))
    except (OSError, TimeoutError):
        return


def _intervals_ms(samples: list[dict[str, Any]], key: str) -> list[float]:
    timestamps = [
        timestamp
        for sample in samples
        if (timestamp := _timestamp(sample.get(key))) is not None
    ]
    return [
        max(0.0, current - previous) * 1000.0
        for previous, current in zip(timestamps, timestamps[1:])
    ]


def _changed_count(samples: list[dict[str, Any]], keys: tuple[str, ...]) -> int:
    previous: tuple[Any, ...] | None = None
    changed = 0
    for sample in samples:
        current = tuple(sample.get(key) for key in keys)
        if previous is not None and current != previous:
            changed += 1
        previous = current
    return changed


def _stat(values: list[float], mode: str) -> float | None:
    if not values:
        return None
    if mode == "median":
        return round(float(statistics.median(values)), 3)
    if mode == "max":
        return round(float(max(values)), 3)
    raise ValueError(mode)


def _latest_unique_rows(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: tuple[Any, ...] | None = None
    for sample in samples:
        identity = (
            sample.get("row_received_at"),
            sample.get("price"),
            sample.get("change_rate"),
            sample.get("trade_time"),
        )
        if identity != previous:
            result.append(sample)
            previous = identity
    return result


def _classify_path(events: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    unique_rows = _latest_unique_rows(rows)
    if not events:
        return "NO_COLLECTOR_OUTPUT_EVENT"
    if not unique_rows:
        return "NO_SSE_ROW"
    latest_event = events[-1]
    latest_row = unique_rows[-1]
    event_ts = _timestamp(latest_event.get("event_ts"))
    row_ts = _timestamp(latest_row.get("row_received_at"))
    latest_matches = (
        latest_event.get("price") == latest_row.get("price")
        and latest_event.get("change_rate") == latest_row.get("change_rate")
    )
    if event_ts is not None and row_ts is not None and event_ts - row_ts > 0.5:
        return "GUARD_OR_WORKER_APPLY_LAG"
    if not latest_matches and _changed_count(events, ("price", "change_rate")) > 0:
        return "LATEST_EVENT_NOT_IN_SSE"
    transport = [
        float(sample["row_transport_ms"])
        for sample in unique_rows
        if _number(sample.get("row_transport_ms")) is not None
    ]
    if transport and statistics.median(transport) > 500.0:
        return "SNAPSHOT_OR_SSE_DELAY"
    if _changed_count(events, ("price", "change_rate")) > 0 and _changed_count(
        unique_rows, ("price", "change_rate")
    ) == 0:
        return "SSE_ROW_NOT_CHANGING"
    return "PATH_ACTIVE"


def _summarize_code(
    code: str,
    name: str,
    events: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    events = sorted(events, key=lambda item: str(item.get("observed_at") or ""))
    rows = sorted(rows, key=lambda item: str(item.get("observed_at") or ""))
    unique_rows = _latest_unique_rows(rows)
    event_intervals = _intervals_ms(events, "event_ts")
    quote_intervals = _intervals_ms(unique_rows, "row_received_at")
    log_delays = [
        float(sample["event_log_observe_delay_ms"])
        for sample in events
        if _number(sample.get("event_log_observe_delay_ms")) is not None
    ]
    transport = [
        float(sample["row_transport_ms"])
        for sample in unique_rows
        if _number(sample.get("row_transport_ms")) is not None
    ]
    latest_event = events[-1] if events else {}
    latest_row = unique_rows[-1] if unique_rows else {}
    return {
        "stock_code": code,
        "stock_name": name or code,
        "path_state": _classify_path(events, unique_rows),
        "collector_output_event_count": len(events),
        "collector_price_rate_change_count": _changed_count(events, ("price", "change_rate")),
        "collector_event_interval_median_ms": _stat(event_intervals, "median"),
        "collector_event_interval_max_ms": _stat(event_intervals, "max"),
        "event_log_observe_delay_median_ms": _stat(log_delays, "median"),
        "event_log_observe_delay_max_ms": _stat(log_delays, "max"),
        "sse_quote_update_count": len(unique_rows),
        "sse_price_rate_change_count": _changed_count(unique_rows, ("price", "change_rate")),
        "quote_received_interval_median_ms": _stat(quote_intervals, "median"),
        "quote_received_interval_max_ms": _stat(quote_intervals, "max"),
        "row_transport_median_ms": _stat(transport, "median"),
        "row_transport_max_ms": _stat(transport, "max"),
        "latest_event_ts": latest_event.get("event_ts"),
        "latest_event_price": latest_event.get("price"),
        "latest_event_rate": latest_event.get("change_rate"),
        "latest_event_trade_time": latest_event.get("trade_time"),
        "latest_event_source": latest_event.get("source_code"),
        "latest_row_received_at": latest_row.get("row_received_at"),
        "latest_sse_observed_at": latest_row.get("observed_at"),
        "latest_row_price": latest_row.get("price"),
        "latest_row_rate": latest_row.get("change_rate"),
        "latest_row_trade_time": latest_row.get("trade_time"),
        "latest_row_source": latest_row.get("source_code"),
        "latest_event_row_match": bool(
            latest_event
            and latest_row
            and latest_event.get("price") == latest_row.get("price")
            and latest_event.get("change_rate") == latest_row.get("change_rate")
        ),
        "last_dropped_trade_reason": latest_row.get("last_dropped_trade_reason"),
    }


def _status_number(payload: dict[str, Any], key: str) -> float:
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    return float(_number(status.get(key)) or 0.0)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace existing StockBoard collector-output -> Worker -> SSE price path"
    )
    parser.add_argument("--codes", default=",".join(DEFAULT_CODES))
    parser.add_argument("--duration-sec", type=float, default=DEFAULT_DURATION_SEC)
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL)
    parser.add_argument("--sse-interval-ms", type=int, default=DEFAULT_SSE_INTERVAL_MS)
    args = parser.parse_args()

    codes = [_code(item) for item in str(args.codes or "").split(",")]
    codes = list(dict.fromkeys(item for item in codes if item))
    if not codes:
        raise RuntimeError("At least one valid six-digit stock code is required")
    duration_sec = max(3.0, min(60.0, float(args.duration_sec or DEFAULT_DURATION_SEC)))

    initial = _fetch_json(args.snapshot_url)
    initial_rows = [row for row in initial.get("rows", []) if isinstance(row, dict)]
    name_by_code = {
        _code(row.get("stock_code")): str(row.get("stock_name") or "")
        for row in initial_rows
        if _code(row.get("stock_code"))
    }
    status = initial.get("status") if isinstance(initial.get("status"), dict) else {}
    event_log_text = str(status.get("event_log_path") or "").strip()
    event_log = (
        Path(event_log_text)
        if event_log_text
        else RUNTIME_DIR / f"events_{datetime.now():%Y%m%d}.jsonl"
    )

    event_samples: list[dict[str, Any]] = []
    row_samples: list[dict[str, Any]] = []
    snapshot_lags: list[float] = []
    stop_event = threading.Event()
    code_set = set(codes)

    tail_thread = threading.Thread(
        target=_tail_event_log,
        args=(event_log, code_set, stop_event, event_samples),
        name="stockboard-price-trace-log-reader",
        daemon=True,
    )
    sse_thread = threading.Thread(
        target=_consume_sse,
        args=(
            _stream_url(args.snapshot_url, args.sse_interval_ms),
            code_set,
            stop_event,
            row_samples,
            snapshot_lags,
        ),
        name="stockboard-price-trace-sse-reader",
        daemon=True,
    )

    print(f"Tracing {','.join(codes)} for {duration_sec:.1f}s using existing event log and SSE...")
    started_at = _now_iso()
    tail_thread.start()
    sse_thread.start()
    time.sleep(duration_sec)
    stop_event.set()
    tail_thread.join(timeout=2.0)
    sse_thread.join(timeout=2.0)
    final = _fetch_json(args.snapshot_url)
    finished_at = _now_iso()

    summaries = [
        _summarize_code(
            code,
            name_by_code.get(code, code),
            [sample for sample in event_samples if sample.get("stock_code") == code],
            [sample for sample in row_samples if sample.get("stock_code") == code],
        )
        for code in codes
    ]
    initial_status = initial.get("status") if isinstance(initial.get("status"), dict) else {}
    final_status = final.get("status") if isinstance(final.get("status"), dict) else {}
    initial_sender = (
        initial_status.get("collector_status", {}).get("sender_stats", {})
        if isinstance(initial_status.get("collector_status"), dict)
        else {}
    )
    final_sender = (
        final_status.get("collector_status", {}).get("sender_stats", {})
        if isinstance(final_status.get("collector_status"), dict)
        else {}
    )
    overall = {
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "codes": codes,
        "event_log_path": str(event_log),
        "sse_interval_ms": max(50, min(2000, int(args.sse_interval_ms))),
        "sse_snapshot_count": len({sample.get("payload_ts") for sample in row_samples if sample.get("payload_ts")}),
        "snapshot_lag_median_ms": _stat(snapshot_lags, "median"),
        "snapshot_lag_max_ms": _stat(snapshot_lags, "max"),
        "event_count_delta": _status_number(final, "event_count") - _status_number(initial, "event_count"),
        "trade_count_delta": _status_number(final, "trade_count") - _status_number(initial, "trade_count"),
        "dropped_trade_count_delta": _status_number(final, "dropped_trade_count") - _status_number(initial, "dropped_trade_count"),
        "trade_field_suppressed_delta": _status_number(final, "trade_field_regression_suppressed_count") - _status_number(initial, "trade_field_regression_suppressed_count"),
        "collector_received_trade_delta": (_number(final_sender.get("received_trade_count")) or 0.0) - (_number(initial_sender.get("received_trade_count")) or 0.0),
        "operator_only_no_production_path_change": True,
    }

    print("\n=== StockBoard 15s price path trace ===\n")
    for key, value in overall.items():
        print(f"{key:40}: {value}")

    print(
        "\nCode   Name                 State                       "
        "Evt  EvtChg EvtMed  SSE  SSEChg SSEMed Transport LatestMatch"
    )
    print(
        "------ -------------------- --------------------------- "
        "---- ------ ------- ---- ------ ------ --------- -----------"
    )
    for row in summaries:
        print(
            f"{row['stock_code']:<6} "
            f"{str(row['stock_name'])[:20]:<20} "
            f"{str(row['path_state'])[:27]:<27} "
            f"{row['collector_output_event_count']:>4} "
            f"{row['collector_price_rate_change_count']:>6} "
            f"{str(row['collector_event_interval_median_ms'] or '-'):>7} "
            f"{row['sse_quote_update_count']:>4} "
            f"{row['sse_price_rate_change_count']:>6} "
            f"{str(row['quote_received_interval_median_ms'] or '-'):>6} "
            f"{str(row['row_transport_median_ms'] or '-'):>9} "
            f"{str(row['latest_event_row_match']):>11}"
        )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RUNTIME_DIR / f"price_path_trace_{stamp}.csv"
    json_path = RUNTIME_DIR / f"price_path_trace_{stamp}.json"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summaries[0].keys()))
        writer.writeheader()
        writer.writerows(summaries)
    json_path.write_text(
        json.dumps(
            {
                "summary": overall,
                "codes": summaries,
                "event_samples": event_samples,
                "sse_row_samples": row_samples,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nCSV_REPORT={csv_path}")
    print(f"JSON_REPORT={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
