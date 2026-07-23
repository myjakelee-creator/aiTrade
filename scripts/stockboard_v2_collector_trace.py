from __future__ import annotations

"""Trace the existing QAx -> Provider -> EventSender -> Worker counter path.

This operator-only tool is never imported by the live Collector or Worker. It adds
no QAx owner, FID, Kiwoom request, WebSocket, production thread, timer, or cadence.
It opens one existing local StockBoard SSE stream and reads the existing snapshot
once before and once after the requested trace window.
"""

import argparse
import csv
import json
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
DEFAULT_SNAPSHOT_URL = "http://127.0.0.1:8765/api/v2/snapshot?limit=1"
DEFAULT_DURATION_SEC = 15.0
DEFAULT_SSE_INTERVAL_MS = 100

_COUNTER_KEYS = (
    "provider_realdata_received_count",
    "provider_trade_event_received_count",
    "provider_trade_event_applied_count",
    "provider_stale_trade_drop_count",
    "provider_older_trade_drop_count",
    "provider_latest_only_dropped_count",
    "provider_store_update_guard_drop_count",
    "sender_received_trade_count",
    "sender_received_orderbook_count",
    "sender_received_direct_count",
    "sender_sent_count",
    "sender_coalesced_trade_overwrite_count",
    "worker_event_count",
    "worker_trade_count",
    "worker_dropped_trade_count",
    "worker_trade_field_suppressed_count",
)


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
                    "limit": 1,
                    "interval_ms": max(50, min(2000, int(interval_ms))),
                    "ts": int(time.time() * 1000),
                }
            ),
            "",
        )
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_sample(payload: dict[str, Any], observed_at: str) -> dict[str, Any]:
    status = _dict(payload.get("status"))
    collector = _dict(status.get("collector_status"))
    provider = _dict(collector.get("status"))
    sender = _dict(collector.get("sender_stats"))
    market_session = _dict(payload.get("market_session"))
    if not market_session:
        market_session = _dict(status.get("market_session"))

    payload_ts = payload.get("ts")
    observed_ts = _timestamp(observed_at)
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
        "phase": market_session.get("phase_label")
        or market_session.get("phase")
        or status.get("market_phase_label"),
        "collector_status_ts": collector.get("ts"),
        "provider_login_state": provider.get("login_state"),
        "provider_realreg_succeeded": provider.get("realreg_succeeded"),
        "provider_realreg_code_count": _number(provider.get("realreg_code_count")),
        "provider_realreg_fids": provider.get("realreg_fids"),
        "provider_qt_pump_running": provider.get("qt_pump_running"),
        "provider_qt_pump_last_at": provider.get("qt_pump_last_at"),
        "provider_realdata_received_count": _number(
            provider.get("realdata_received_count")
        ),
        "provider_trade_event_received_count": _number(
            provider.get("trade_event_received_count")
        ),
        "provider_trade_event_applied_count": _number(
            provider.get("trade_event_applied_count")
        ),
        "provider_stale_trade_drop_count": _number(
            provider.get("stale_trade_drop_count")
        ),
        "provider_older_trade_drop_count": _number(
            provider.get("older_trade_drop_count")
        ),
        "provider_latest_only_dropped_count": _number(
            provider.get("latest_only_dropped_count")
        ),
        "provider_store_update_guard_drop_count": _number(
            provider.get("store_update_guard_drop_count")
        ),
        "provider_realdata_last_received_at": provider.get(
            "realdata_last_received_at"
        ),
        "provider_realdata_last_real_type": provider.get("realdata_last_real_type"),
        "provider_realdata_last_code": provider.get("realdata_last_code"),
        "provider_realdata_parse_error": provider.get("realdata_parse_error"),
        "provider_trade_last_received_at": provider.get("trade_last_received_at"),
        "provider_trade_last_received_code": provider.get(
            "trade_last_received_code"
        ),
        "provider_trade_last_normalized_code": provider.get(
            "trade_last_normalized_code"
        ),
        "provider_trade_last_fid10_raw": provider.get("trade_last_fid10_raw"),
        "provider_trade_last_fid20_raw": provider.get("trade_last_fid20_raw"),
        "provider_trade_last_sample": provider.get("trade_last_sample"),
        "sender_connected": sender.get("connected"),
        "sender_flush_ms": _number(sender.get("flush_ms")),
        "sender_received_trade_count": _number(sender.get("received_trade_count")),
        "sender_received_orderbook_count": _number(
            sender.get("received_orderbook_count")
        ),
        "sender_received_direct_count": _number(sender.get("received_direct_count")),
        "sender_sent_count": _number(sender.get("sent_count")),
        "sender_coalesced_trade_overwrite_count": _number(
            sender.get("coalesced_trade_overwrite_count")
        ),
        "sender_pending_trade_count": _number(sender.get("pending_trade_count")),
        "sender_pending_total_count": _number(sender.get("pending_total_count")),
        "sender_last_flush_at": sender.get("last_flush_at"),
        "sender_last_error": sender.get("last_error"),
        "worker_event_count": _number(status.get("event_count")),
        "worker_trade_count": _number(status.get("trade_count")),
        "worker_dropped_trade_count": _number(status.get("dropped_trade_count")),
        "worker_trade_field_suppressed_count": _number(
            status.get("trade_field_regression_suppressed_count")
        ),
        "worker_last_event_received_at": status.get("last_event_received_at")
        or status.get("last_event_at"),
        "worker_last_trade_event_received_at": status.get(
            "last_trade_event_received_at"
        ),
    }


def _sample_signature(sample: dict[str, Any]) -> tuple[Any, ...]:
    return (
        sample.get("collector_status_ts"),
        sample.get("provider_realdata_received_count"),
        sample.get("provider_trade_event_received_count"),
        sample.get("provider_trade_event_applied_count"),
        sample.get("sender_received_trade_count"),
        sample.get("sender_sent_count"),
        sample.get("worker_trade_count"),
    )


def _consume_sse(
    url: str,
    stop_event: threading.Event,
    output: list[dict[str, Any]],
) -> None:
    request = Request(url, headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"})
    last_signature: tuple[Any, ...] | None = None
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
                sample = _extract_sample(payload, _now_iso())
                signature = _sample_signature(sample)
                if signature != last_signature:
                    output.append(sample)
                    last_signature = signature
    except (OSError, TimeoutError):
        return


def _delta(first: dict[str, Any], last: dict[str, Any], key: str) -> float:
    start = _number(first.get(key)) or 0.0
    end = _number(last.get(key)) or 0.0
    return end - start


def _age_sec(value: Any, observed_at: Any) -> float | None:
    source = _timestamp(value)
    observed = _timestamp(observed_at)
    if source is None or observed is None:
        return None
    return round(max(0.0, observed - source), 3)


def _classify(first: dict[str, Any], last: dict[str, Any]) -> str:
    if last.get("provider_login_state") != "connected":
        return "QAX_LOGIN_NOT_CONNECTED"
    if last.get("provider_realreg_succeeded") is not True:
        return "SETREALREG_NOT_READY"
    if last.get("provider_qt_pump_running") is not True:
        return "QT_EVENT_PUMP_NOT_RUNNING"
    if last.get("sender_connected") is not True:
        return "EVENT_SENDER_NOT_CONNECTED"

    realdata_delta = _delta(first, last, "provider_realdata_received_count")
    trade_callback_delta = _delta(
        first, last, "provider_trade_event_received_count"
    )
    applied_delta = _delta(first, last, "provider_trade_event_applied_count")
    sender_trade_delta = _delta(first, last, "sender_received_trade_count")
    worker_trade_delta = _delta(first, last, "worker_trade_count")

    if realdata_delta <= 0:
        return "NO_QAX_REALDATA_CALLBACK"
    if trade_callback_delta <= 0:
        return "QAX_REALDATA_WITHOUT_STOCK_TRADE"
    if applied_delta <= 0:
        return "PROVIDER_TRADE_NOT_APPLIED"
    if sender_trade_delta <= 0:
        return "STORE_TO_EVENT_SENDER_GAP"
    if worker_trade_delta <= 0:
        pending = _number(last.get("sender_pending_trade_count")) or 0.0
        if pending > 0:
            return "EVENT_SENDER_PENDING_TRADE"
        return "EVENT_SENDER_OR_WORKER_GAP"
    return "PRICE_PATH_ACTIVE"


def _fmt(value: Any, digits: int = 0) -> str:
    number = _number(value)
    if number is None:
        return "-"
    if digits <= 0:
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace existing StockBoard QAx/Provider/EventSender/Worker counters"
    )
    parser.add_argument("--duration-sec", type=float, default=DEFAULT_DURATION_SEC)
    parser.add_argument("--snapshot-url", default=DEFAULT_SNAPSHOT_URL)
    parser.add_argument("--sse-interval-ms", type=int, default=DEFAULT_SSE_INTERVAL_MS)
    args = parser.parse_args()

    duration_sec = max(3.0, min(60.0, float(args.duration_sec or DEFAULT_DURATION_SEC)))
    interval_ms = max(50, min(2000, int(args.sse_interval_ms or DEFAULT_SSE_INTERVAL_MS)))

    initial_payload = _fetch_json(args.snapshot_url)
    initial = _extract_sample(initial_payload, _now_iso())
    samples: list[dict[str, Any]] = [initial]
    stop_event = threading.Event()
    sse_thread = threading.Thread(
        target=_consume_sse,
        args=(_stream_url(args.snapshot_url, interval_ms), stop_event, samples),
        name="stockboard-collector-trace-sse-reader",
        daemon=True,
    )

    print(
        f"Tracing existing QAx/Provider/EventSender/Worker counters for {duration_sec:.1f}s..."
    )
    sse_thread.start()
    time.sleep(duration_sec)
    stop_event.set()
    sse_thread.join(timeout=2.0)

    final_payload = _fetch_json(args.snapshot_url)
    final = _extract_sample(final_payload, _now_iso())
    if _sample_signature(final) != _sample_signature(samples[-1]):
        samples.append(final)
    else:
        samples[-1] = final

    first = samples[0]
    last = samples[-1]
    deltas = {f"{key}_delta": _delta(first, last, key) for key in _COUNTER_KEYS}
    result = {
        "started_at": first.get("observed_at"),
        "finished_at": last.get("observed_at"),
        "duration_sec": duration_sec,
        "sample_count": len(samples),
        "phase": last.get("phase"),
        "path_state": _classify(first, last),
        **deltas,
        "provider_login_state": last.get("provider_login_state"),
        "provider_realreg_succeeded": last.get("provider_realreg_succeeded"),
        "provider_realreg_code_count": last.get("provider_realreg_code_count"),
        "provider_realreg_fids": last.get("provider_realreg_fids"),
        "provider_qt_pump_running": last.get("provider_qt_pump_running"),
        "provider_qt_pump_age_sec": _age_sec(
            last.get("provider_qt_pump_last_at"), last.get("observed_at")
        ),
        "provider_realdata_last_age_sec": _age_sec(
            last.get("provider_realdata_last_received_at"), last.get("observed_at")
        ),
        "provider_trade_last_age_sec": _age_sec(
            last.get("provider_trade_last_received_at"), last.get("observed_at")
        ),
        "provider_realdata_last_real_type": last.get(
            "provider_realdata_last_real_type"
        ),
        "provider_trade_last_received_code": last.get(
            "provider_trade_last_received_code"
        ),
        "provider_trade_last_normalized_code": last.get(
            "provider_trade_last_normalized_code"
        ),
        "provider_trade_last_fid10_raw": last.get("provider_trade_last_fid10_raw"),
        "provider_trade_last_fid20_raw": last.get("provider_trade_last_fid20_raw"),
        "provider_realdata_parse_error": last.get("provider_realdata_parse_error"),
        "sender_connected": last.get("sender_connected"),
        "sender_pending_trade_count": last.get("sender_pending_trade_count"),
        "sender_pending_total_count": last.get("sender_pending_total_count"),
        "sender_last_error": last.get("sender_last_error"),
        "operator_only_no_production_path_change": True,
    }

    print("\n=== StockBoard QAx / Collector 15s trace ===\n")
    for key, value in result.items():
        print(f"{key:43}: {value}")

    print(
        "\nTime         QAxAll TradeCb Applied SenderRecv Sent WorkerTrade Pending LastType LastCode FID10 FID20"
    )
    print(
        "------------ ------ ------- ------- ---------- ---- ----------- ------- -------- -------- ----- -----"
    )
    for sample in samples:
        observed = str(sample.get("observed_at") or "")
        time_text = observed[11:23] if len(observed) >= 23 else observed
        print(
            f"{time_text:<12} "
            f"{_fmt(sample.get('provider_realdata_received_count')):>6} "
            f"{_fmt(sample.get('provider_trade_event_received_count')):>7} "
            f"{_fmt(sample.get('provider_trade_event_applied_count')):>7} "
            f"{_fmt(sample.get('sender_received_trade_count')):>10} "
            f"{_fmt(sample.get('sender_sent_count')):>4} "
            f"{_fmt(sample.get('worker_trade_count')):>11} "
            f"{_fmt(sample.get('sender_pending_trade_count')):>7} "
            f"{str(sample.get('provider_realdata_last_real_type') or '-')[:8]:<8} "
            f"{str(sample.get('provider_trade_last_received_code') or '-')[:8]:<8} "
            f"{str(sample.get('provider_trade_last_fid10_raw') or '-')[:5]:>5} "
            f"{str(sample.get('provider_trade_last_fid20_raw') or '-')[:5]:>5}"
        )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RUNTIME_DIR / f"collector_path_trace_{stamp}.csv"
    json_path = RUNTIME_DIR / f"collector_path_trace_{stamp}.json"
    fieldnames = list(samples[0].keys()) if samples else []
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(samples)
    json_path.write_text(
        json.dumps({"summary": result, "samples": samples}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nCSV_REPORT={csv_path}")
    print(f"JSON_REPORT={json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
