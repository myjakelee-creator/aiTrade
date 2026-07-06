"""Opening-session latency probe for StockBoard.

This script is deliberately outside the live server path. It samples the existing
HTTP APIs and writes CSV/JSONL evidence that separates these stages:

1. client -> StockBoard HTTP request latency
2. StockBoard API payload creation time when provided by the endpoint
3. RealtimeStore freshness via price_received_at / trade_received_at / sequence
4. top100 display freshness via display_price / display_change_rate fields
5. provider status counters and direct API health

It does not replace full in-process instrumentation. It is the first safe layer
that can run during live trading without touching the Kiwoom event thread.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

KST = timezone(timedelta(hours=9))

DEFAULT_CODES = (
    "000660",  # SK하이닉스
    "005930",  # 삼성전자
    "009150",  # 삼성전기
    "402340",  # SK스퀘어
    "005380",  # 현대차
    "010140",  # 삼성중공업
    "196170",  # 알테오젠
    "015760",  # 한국전력
    "042660",  # 한화오션
    "010120",  # LS ELECTRIC
    "011070",  # LG이노텍
    "034020",  # 두산에너빌리티
    "042700",  # 한미반도체
)

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_OUTPUT_DIR = "data/runtime/latency"
DEFAULT_MODEL = "NET_BUY_STRENGTH_V02"


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def epoch_ms() -> int:
    return int(time.time() * 1000)


def parse_code_list(value: str | list[str] | tuple[str, ...] | None) -> list[str]:
    """Parse quoted or unquoted PowerShell code arguments safely.

    PowerShell can split comma-separated native-command args in surprising ways
    when the value is not quoted. Accept both a single CSV string and multiple
    positional values consumed by argparse nargs='*'.
    """
    if value is None or value == []:
        return list(DEFAULT_CODES)
    raw_items: list[str]
    if isinstance(value, (list, tuple)):
        raw_items = [str(item) for item in value]
    else:
        raw_items = [str(value)]
    codes: list[str] = []
    for item in raw_items:
        for raw in item.split(","):
            code = raw.strip().upper()
            if code.startswith("A") and len(code) == 7:
                code = code[1:]
            code = code.replace("_AL", "").replace("_NX", "")
            if len(code) == 6 and code.isdigit() and code not in codes:
                codes.append(code)
    return codes or list(DEFAULT_CODES)


def number_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def age_seconds(timestamp_text: Any, now_epoch_ms: int | None = None) -> float | None:
    if not timestamp_text:
        return None
    text = str(timestamp_text).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    base = (now_epoch_ms or epoch_ms()) / 1000
    return round(max(0.0, base - dt.timestamp()), 3)


def pct(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    value = ordered[lower] * (1 - weight) + ordered[upper] * weight
    return round(value, 3)


def get_json(url: str, timeout: float) -> tuple[dict[str, Any] | list[Any] | None, str | None, float]:
    start = time.perf_counter()
    try:
        request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.perf_counter() - start) * 1000
            return json.loads(raw), None, elapsed_ms
    except Exception as error:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return None, str(error), elapsed_ms


def as_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        rows = payload.get("rows") or payload.get("data") or payload.get("quotes")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, dict):
            result = []
            for code, quote in rows.items():
                if isinstance(quote, dict):
                    row = {"stock_code": code}
                    row.update(quote)
                    result.append(row)
            return result
    return []


def row_code(row: dict[str, Any]) -> str:
    value = row.get("stock_code") or row.get("code") or row.get("normalized_code")
    text = str(value or "").strip().upper().replace("_AL", "").replace("_NX", "")
    if text.startswith("A") and len(text) == 7:
        text = text[1:]
    return text if len(text) == 6 and text.isdigit() else ""


def extract_sample(
    *,
    endpoint: str,
    payload: Any,
    request_elapsed_ms: float,
    requested_at_epoch_ms: int,
    received_at_epoch_ms: int,
    error: str | None,
    target_codes: set[str],
) -> list[dict[str, Any]]:
    rows = as_rows(payload)
    samples: list[dict[str, Any]] = []
    for row in rows:
        code = row_code(row)
        if target_codes and code not in target_codes:
            continue
        price_received_at = row.get("price_received_at") or row.get("received_at")
        trade_received_at = row.get("trade_received_at") or row.get("received_at")
        api_created_ms = number_or_none(row.get("api_patch_created_at_epoch_ms"))
        sample = {
            "ts_utc": utc_iso(),
            "endpoint": endpoint,
            "stock_code": code,
            "stock_name": row.get("stock_name") or row.get("name") or "",
            "request_elapsed_ms": round(request_elapsed_ms, 3),
            "requested_at_epoch_ms": requested_at_epoch_ms,
            "received_at_epoch_ms": received_at_epoch_ms,
            "server_to_client_ms": round(received_at_epoch_ms - api_created_ms, 3) if api_created_ms is not None else None,
            "error": error,
            "price": row.get("display_price") if row.get("display_price") not in (None, "") else row.get("price"),
            "change_rate": row.get("display_change_rate") if row.get("display_change_rate") not in (None, "") else row.get("change_rate"),
            "realtime_price": row.get("realtime_price") or row.get("price"),
            "realtime_change_rate": row.get("realtime_change_rate") or row.get("change_rate"),
            "trade_value_eok": row.get("trade_value_eok") or row.get("realtime_acc_trade_value_eok_candidate"),
            "price_source": row.get("price_source") or row.get("display_price_source") or row.get("realtime_source") or "",
            "realtime_source_code": row.get("realtime_source_code") or row.get("source_code") or "",
            "price_received_at": price_received_at,
            "trade_received_at": trade_received_at,
            "price_age_sec_client": age_seconds(price_received_at, received_at_epoch_ms),
            "trade_age_sec_client": age_seconds(trade_received_at, received_at_epoch_ms),
            "price_age_sec_api": row.get("price_age_sec"),
            "price_sequence": row.get("price_sequence"),
            "trade_sequence": row.get("trade_sequence") or row.get("sequence"),
            "lane": row.get("api_lane") or row.get("lane") or endpoint,
            "fid20_trade_lag_sec": row.get("fid20_trade_lag_sec") or row.get("trade_lag_sec"),
            "stale_trade_suspect": row.get("stale_trade_suspect"),
            "one_min_strength": row.get("one_min_strength"),
            "program_net": row.get("program_net"),
            "candidate_grade_text": row.get("candidate_grade_text"),
            "score_percent": row.get("score_percent"),
        }
        samples.append(sample)
    if not samples and error:
        samples.append({
            "ts_utc": utc_iso(),
            "endpoint": endpoint,
            "stock_code": "",
            "stock_name": "",
            "request_elapsed_ms": round(request_elapsed_ms, 3),
            "requested_at_epoch_ms": requested_at_epoch_ms,
            "received_at_epoch_ms": received_at_epoch_ms,
            "server_to_client_ms": None,
            "error": error,
        })
    return samples


@dataclass
class ProbeConfig:
    base_url: str
    codes: list[str]
    interval_sec: float
    duration_sec: float
    output_dir: Path
    model: str
    top100_every: int
    provider_every: int
    timeout_sec: float


def build_urls(config: ProbeConfig, iteration: int) -> list[tuple[str, str]]:
    codes = ",".join(config.codes)
    encoded_model = urllib.parse.quote(config.model)
    urls = [
        ("realtime", f"{config.base_url}/api/realtime?codes={codes}&debug=1&ts={epoch_ms()}"),
        ("price_light", f"{config.base_url}/api/price_light_patch?codes={codes}&ts={epoch_ms()}"),
    ]
    if config.top100_every > 0 and iteration % config.top100_every == 0:
        urls.append(("top100", f"{config.base_url}/api/top100?candidate_model={encoded_model}&ts={epoch_ms()}"))
    if config.provider_every > 0 and iteration % config.provider_every == 0:
        urls.append(("provider_status", f"{config.base_url}/api/realtime_provider_status?ts={epoch_ms()}"))
    return urls


def write_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def summarize(csv_path: Path) -> dict[str, Any]:
    if not csv_path.exists():
        return {"error": f"missing csv: {csv_path}"}
    groups: dict[tuple[str, str], dict[str, list[float]]] = {}
    rows = 0
    metric_keys = [
        "request_elapsed_ms",
        "price_age_sec_client",
        "trade_age_sec_client",
        "price_age_sec_api",
        "fid20_trade_lag_sec",
        "server_to_client_ms",
    ]
    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rows += 1
            code = row.get("stock_code") or ""
            endpoint = row.get("endpoint") or ""
            if not code:
                continue
            bucket = groups.setdefault((code, endpoint), {key: [] for key in metric_keys})
            for key in metric_keys:
                value = number_or_none(row.get(key))
                if value is not None:
                    bucket[key].append(value)
    summary_rows = []
    by_code_rows = []
    by_code_metrics: dict[str, dict[str, list[float]]] = {}
    for (code, endpoint), metrics in sorted(groups.items()):
        item = {"stock_code": code, "endpoint": endpoint}
        code_bucket = by_code_metrics.setdefault(code, {key: [] for key in metric_keys})
        for key, values in metrics.items():
            code_bucket[key].extend(values)
            item[f"{key}_p50"] = pct(values, 0.50)
            item[f"{key}_p95"] = pct(values, 0.95)
            item[f"{key}_max"] = round(max(values), 3) if values else None
        summary_rows.append(item)
    for code, metrics in sorted(by_code_metrics.items()):
        item = {"stock_code": code}
        for key, values in metrics.items():
            item[f"{key}_p50"] = pct(values, 0.50)
            item[f"{key}_p95"] = pct(values, 0.95)
            item[f"{key}_max"] = round(max(values), 3) if values else None
        by_code_rows.append(item)
    return {
        "sample_rows": rows,
        "code_count": len(by_code_metrics),
        "summary_by_code": by_code_rows,
        "summary_by_code_endpoint": summary_rows,
    }


def run_probe(config: ProbeConfig) -> Path:
    target_codes = set(config.codes)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    date_text = time.strftime("%Y%m%d")
    csv_path = config.output_dir / f"opening_latency_samples_{date_text}.csv"
    jsonl_path = config.output_dir / f"opening_latency_raw_{date_text}.jsonl"
    summary_path = config.output_dir / f"opening_latency_summary_{date_text}.json"

    fieldnames = [
        "ts_utc", "endpoint", "stock_code", "stock_name", "request_elapsed_ms",
        "requested_at_epoch_ms", "received_at_epoch_ms", "server_to_client_ms", "error",
        "price", "change_rate", "realtime_price", "realtime_change_rate", "trade_value_eok",
        "price_source", "realtime_source_code", "price_received_at", "trade_received_at",
        "price_age_sec_client", "trade_age_sec_client", "price_age_sec_api",
        "price_sequence", "trade_sequence", "lane", "fid20_trade_lag_sec",
        "stale_trade_suspect", "one_min_strength", "program_net", "candidate_grade_text",
        "score_percent",
    ]

    started = time.monotonic()
    iteration = 0
    print(f"StockBoard opening latency probe started: {utc_iso()}")
    print(f"base_url={config.base_url}")
    print(f"codes={','.join(config.codes)}")
    print(f"csv={csv_path}")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            if config.duration_sec > 0 and time.monotonic() - started >= config.duration_sec:
                break
            iteration += 1
            all_samples: list[dict[str, Any]] = []
            raw_packet = {
                "ts_utc": utc_iso(),
                "iteration": iteration,
                "endpoints": [],
            }
            for endpoint, url in build_urls(config, iteration):
                requested_at = epoch_ms()
                payload, error, elapsed_ms = get_json(url, config.timeout_sec)
                received_at = epoch_ms()
                raw_packet["endpoints"].append({
                    "endpoint": endpoint,
                    "url": url,
                    "request_elapsed_ms": round(elapsed_ms, 3),
                    "error": error,
                    "payload": payload if endpoint in {"provider_status"} else None,
                })
                all_samples.extend(
                    extract_sample(
                        endpoint=endpoint,
                        payload=payload,
                        request_elapsed_ms=elapsed_ms,
                        requested_at_epoch_ms=requested_at,
                        received_at_epoch_ms=received_at,
                        error=error,
                        target_codes=target_codes,
                    )
                )
            if all_samples:
                write_rows(csv_path, all_samples, fieldnames)
                with jsonl_path.open("a", encoding="utf-8") as file:
                    file.write(json.dumps(raw_packet, ensure_ascii=False) + "\n")
            if iteration % max(1, int(5 / max(config.interval_sec, 0.1))) == 0:
                recent_ages = [number_or_none(row.get("price_age_sec_client")) for row in all_samples]
                recent_ages = [value for value in recent_ages if value is not None]
                age_text = f"p95_age={pct(recent_ages, 0.95)}s" if recent_ages else "p95_age=-"
                print(f"{utc_iso()} iter={iteration} samples={len(all_samples)} {age_text}")
            time.sleep(max(0.05, config.interval_sec))
    except KeyboardInterrupt:
        print("stopped by user")

    summary = summarize(csv_path)
    summary.update({
        "created_at": utc_iso(),
        "base_url": config.base_url,
        "codes": config.codes,
        "interval_sec": config.interval_sec,
        "duration_sec": config.duration_sec,
        "csv_path": str(csv_path),
        "jsonl_path": str(jsonl_path),
        "timestamp_policy": "naive server timestamps are interpreted as KST",
    })
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"summary={summary_path}")
    return summary_path


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StockBoard opening latency probe")
    parser.add_argument("--base-url", default=os.getenv("STOCKBOARD_PROBE_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--codes", nargs="*", default=None)
    parser.add_argument("--interval", type=float, default=float(os.getenv("STOCKBOARD_PROBE_INTERVAL_SEC", "0.5")))
    parser.add_argument("--duration", type=float, default=float(os.getenv("STOCKBOARD_PROBE_DURATION_SEC", "1800")))
    parser.add_argument("--output-dir", default=os.getenv("STOCKBOARD_PROBE_OUTPUT_DIR", DEFAULT_OUTPUT_DIR))
    parser.add_argument("--model", default=os.getenv("STOCKBOARD_PROBE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--top100-every", type=int, default=int(os.getenv("STOCKBOARD_PROBE_TOP100_EVERY", "20")))
    parser.add_argument("--provider-every", type=int, default=int(os.getenv("STOCKBOARD_PROBE_PROVIDER_EVERY", "10")))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("STOCKBOARD_PROBE_TIMEOUT_SEC", "2.0")))
    args = parser.parse_args(list(argv) if argv is not None else None)

    env_codes = os.getenv("STOCKBOARD_PROBE_CODES")
    code_arg = args.codes if args.codes else env_codes
    config = ProbeConfig(
        base_url=args.base_url.rstrip("/"),
        codes=parse_code_list(code_arg),
        interval_sec=args.interval,
        duration_sec=args.duration,
        output_dir=Path(args.output_dir),
        model=args.model,
        top100_every=args.top100_every,
        provider_every=args.provider_every,
        timeout_sec=args.timeout,
    )
    run_probe(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
