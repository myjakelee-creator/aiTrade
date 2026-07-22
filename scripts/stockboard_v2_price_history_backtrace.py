from __future__ import annotations

"""Backtrace a saved StockBoard/Kiwoom comparison against the existing event log.

This operator-only tool performs no network request and is never imported by the live
Collector or Worker. It reads one saved price_compare JSON report plus the matching
Worker events_YYYYMMDD.jsonl file to determine whether a stale local row already had a
newer Collector-output event available at the comparison time.

The JSONL log contains raw Collector-output events, not an explicit guard decision.
Therefore WORKER_OR_GUARD_NOT_APPLIED is an evidence-based inference, not direct proof
of the exact guard branch that handled the event.
"""

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUNTIME_DIR = ROOT / "data" / "runtime" / "stockboard_v2"
DEFAULT_LOOKBACK_SEC = 300.0
DEFAULT_LOOKAHEAD_SEC = 60.0
DEFAULT_STALE_SEC = 5.0


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


def _code(value: Any) -> str:
    text = "".join(str(value or "").split()).upper().split("_", 1)[0]
    if text.startswith("A"):
        text = text[1:]
    return text.zfill(6) if text.isdigit() and len(text) <= 6 else ""


def _event_values(event: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    values = event.get("values")
    kwargs = event.get("kwargs")
    if isinstance(values, dict):
        result.update(values)
    if isinstance(kwargs, dict):
        result.update(kwargs)
    return result


def _event_sample(event: dict[str, Any]) -> dict[str, Any] | None:
    if event.get("type") != "trade":
        return None
    values = _event_values(event)
    raw = values.get("raw") if isinstance(values.get("raw"), dict) else values
    code = _code(
        event.get("stock_code")
        or event.get("received_code")
        or values.get("stock_code")
        or values.get("normalized_code")
        or values.get("received_code")
    )
    event_ts = event.get("ts")
    if not code or _timestamp(event_ts) is None:
        return None
    return {
        "stock_code": code,
        "event_ts": event_ts,
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
        "trade_value_eok": _number(values.get("trade_value_eok")),
        "source_code": values.get("source_code")
        or values.get("registered_code")
        or event.get("received_code"),
    }


def _latest_compare_report(path_text: str) -> Path:
    if path_text:
        path = Path(path_text)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    candidates = sorted(
        RUNTIME_DIR.glob("price_compare_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError("no price_compare_*.json report found")
    return candidates[0]


def _load_compare(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise RuntimeError("price comparison report must be an object")
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    target_text = (
        summary.get("snapshot_after_ts")
        or summary.get("time")
        or summary.get("snapshot_before_ts")
    )
    target_ts = _timestamp(target_text)
    if target_ts is None:
        raise RuntimeError("comparison report has no usable target timestamp")
    return summary, rows, target_ts


def _event_log_path(path_text: str, target_ts: float) -> Path:
    if path_text:
        path = Path(path_text)
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    target = datetime.fromtimestamp(target_ts).astimezone()
    path = RUNTIME_DIR / f"events_{target:%Y%m%d}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _scan_events(
    path: Path,
    codes: set[str],
    target_ts: float,
    lookback_sec: float,
    lookahead_sec: float,
) -> dict[str, dict[str, Any]]:
    state = {
        code: {
            "last_before": None,
            "first_after": None,
            "window_count": 0,
            "window_price_rate_change_count": 0,
            "_window_last_identity": None,
        }
        for code in codes
    }
    window_start = target_ts - lookback_sec
    window_end = target_ts + lookahead_sec
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                raw_event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw_event, dict):
                continue
            sample = _event_sample(raw_event)
            if not sample:
                continue
            code = sample["stock_code"]
            if code not in state:
                continue
            event_ts = _timestamp(sample.get("event_ts"))
            if event_ts is None:
                continue
            entry = state[code]
            if event_ts <= target_ts:
                entry["last_before"] = sample
            elif entry["first_after"] is None:
                entry["first_after"] = sample
            if window_start <= event_ts <= window_end:
                entry["window_count"] += 1
                identity = (sample.get("price"), sample.get("change_rate"))
                if (
                    entry["_window_last_identity"] is not None
                    and identity != entry["_window_last_identity"]
                ):
                    entry["window_price_rate_change_count"] += 1
                entry["_window_last_identity"] = identity
    for entry in state.values():
        entry.pop("_window_last_identity", None)
    return state


def _age(target_ts: float, value: Any) -> float | None:
    source = _timestamp(value)
    if source is None:
        return None
    return round(target_ts - source, 3)


def _matches(
    left_price: Any,
    left_rate: Any,
    right_price: Any,
    right_rate: Any,
) -> bool:
    lp = _number(left_price)
    rp = _number(right_price)
    lr = _number(left_rate)
    rr = _number(right_rate)
    price_ok = lp is not None and rp is not None and lp == rp
    rate_ok = lr is not None and rr is not None and abs(lr - rr) <= 0.011
    return price_ok and rate_ok


def _classify(
    row: dict[str, Any],
    last_event: dict[str, Any] | None,
    target_ts: float,
    stale_sec: float,
) -> tuple[str, str]:
    if not last_event:
        return "NO_COLLECTOR_EVENT_BEFORE_TARGET", "no trade event exists before target"

    event_age = _age(target_ts, last_event.get("event_ts"))
    if event_age is None:
        return "EVENT_TIMESTAMP_UNKNOWN", "last event has no usable timestamp"
    if event_age > stale_sec:
        return (
            "COLLECTOR_OUTPUT_GAP_AT_TARGET",
            f"last Collector-output event was {event_age:.3f}s old",
        )

    event_matches_local = _matches(
        last_event.get("price"),
        last_event.get("change_rate"),
        row.get("local_price"),
        row.get("local_change_rate"),
    )
    event_matches_kiwoom = _matches(
        last_event.get("price"),
        last_event.get("change_rate"),
        row.get("rest_price"),
        row.get("rest_change_rate"),
    )
    local_matches_kiwoom = _matches(
        row.get("local_price"),
        row.get("local_change_rate"),
        row.get("rest_price"),
        row.get("rest_change_rate"),
    )

    local_received = _timestamp(row.get("local_received_at"))
    event_timestamp = _timestamp(last_event.get("event_ts"))
    event_newer_than_local = bool(
        local_received is not None
        and event_timestamp is not None
        and event_timestamp - local_received > 0.5
    )

    if event_matches_kiwoom and not event_matches_local:
        return (
            "WORKER_OR_GUARD_NOT_APPLIED",
            "recent Collector-output matches Kiwoom but not the saved local row",
        )
    if event_newer_than_local and not event_matches_local:
        return (
            "WORKER_OR_GUARD_NOT_APPLIED",
            "recent Collector-output is newer than the local row and has different values",
        )
    if event_matches_local and local_matches_kiwoom:
        return "PATH_MATCHED_AT_TARGET", "Collector-output, local row and Kiwoom match"
    if event_matches_local:
        return (
            "COLLECTOR_AND_LOCAL_MATCH_KIWOOM_DIFFERED",
            "Collector-output reached the local row; Kiwoom differed at comparison time",
        )
    return (
        "RECENT_EVENT_LOCAL_RELATION_UNRESOLVED",
        "recent event exists but available saved fields do not prove the exact apply outcome",
    )


def _row_result(
    row: dict[str, Any],
    event_state: dict[str, Any],
    target_ts: float,
    stale_sec: float,
) -> dict[str, Any]:
    last_event = event_state.get("last_before")
    first_after = event_state.get("first_after")
    state, evidence = _classify(row, last_event, target_ts, stale_sec)
    local_received_at = row.get("local_received_at")
    return {
        "local_rank": _number(row.get("local_rank")),
        "stock_code": _code(row.get("stock_code")),
        "stock_name": row.get("stock_name"),
        "state": state,
        "evidence": evidence,
        "inference_only": state == "WORKER_OR_GUARD_NOT_APPLIED",
        "target_time": datetime.fromtimestamp(target_ts).astimezone().isoformat(
            timespec="milliseconds"
        ),
        "local_received_at": local_received_at,
        "local_age_at_target_sec": _age(target_ts, local_received_at),
        "last_event_ts": last_event.get("event_ts") if last_event else None,
        "last_event_age_at_target_sec": (
            _age(target_ts, last_event.get("event_ts")) if last_event else None
        ),
        "first_event_after_ts": first_after.get("event_ts") if first_after else None,
        "first_event_after_target_sec": (
            round(_timestamp(first_after.get("event_ts")) - target_ts, 3)
            if first_after and _timestamp(first_after.get("event_ts")) is not None
            else None
        ),
        "window_event_count": event_state.get("window_count", 0),
        "window_price_rate_change_count": event_state.get(
            "window_price_rate_change_count", 0
        ),
        "event_price": last_event.get("price") if last_event else None,
        "local_price": _number(row.get("local_price")),
        "kiwoom_price": _number(row.get("rest_price")),
        "event_change_rate": last_event.get("change_rate") if last_event else None,
        "local_change_rate": _number(row.get("local_change_rate")),
        "kiwoom_change_rate": _number(row.get("rest_change_rate")),
        "event_trade_time": last_event.get("trade_time") if last_event else None,
        "event_source_code": last_event.get("source_code") if last_event else None,
    }


def _fmt(value: Any, digits: int = 1) -> str:
    number = _number(value)
    if number is None:
        return "-"
    if digits <= 0:
        return f"{number:,.0f}"
    return f"{number:,.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backtrace a saved price comparison against the existing event log"
    )
    parser.add_argument("--compare-json", default="")
    parser.add_argument("--event-log", default="")
    parser.add_argument("--lookback-sec", type=float, default=DEFAULT_LOOKBACK_SEC)
    parser.add_argument("--lookahead-sec", type=float, default=DEFAULT_LOOKAHEAD_SEC)
    parser.add_argument("--stale-sec", type=float, default=DEFAULT_STALE_SEC)
    args = parser.parse_args()

    compare_path = _latest_compare_report(args.compare_json)
    summary, compare_rows, target_ts = _load_compare(compare_path)
    event_path = _event_log_path(args.event_log, target_ts)
    lookback_sec = max(1.0, min(3600.0, float(args.lookback_sec)))
    lookahead_sec = max(0.0, min(600.0, float(args.lookahead_sec)))
    stale_sec = max(0.1, min(300.0, float(args.stale_sec)))

    codes = {_code(row.get("stock_code")) for row in compare_rows}
    codes.discard("")
    event_states = _scan_events(
        event_path, codes, target_ts, lookback_sec, lookahead_sec
    )
    results = [
        _row_result(
            row,
            event_states.get(_code(row.get("stock_code")), {}),
            target_ts,
            stale_sec,
        )
        for row in compare_rows
        if _code(row.get("stock_code"))
    ]
    results.sort(key=lambda row: (row.get("local_rank") or 999999, row["stock_code"]))
    state_counts = dict(Counter(row["state"] for row in results))
    output_summary = {
        "compare_report": str(compare_path),
        "event_log": str(event_path),
        "target_time": datetime.fromtimestamp(target_ts).astimezone().isoformat(
            timespec="milliseconds"
        ),
        "row_count": len(results),
        "lookback_sec": lookback_sec,
        "lookahead_sec": lookahead_sec,
        "stale_sec": stale_sec,
        "state_counts": state_counts,
        "source_comparison_summary": summary,
        "operator_only_no_production_path_change": True,
        "guard_decision_directly_logged": False,
    }

    print("\n=== StockBoard historical price event backtrace ===\n")
    for key, value in output_summary.items():
        if key != "source_comparison_summary":
            print(f"{key:42}: {value}")
    print(
        "\nRank Code   Name                 State                                      EventAge LocalAge EventPrice LocalPrice KiwoomPrice"
    )
    print(
        "---- ------ -------------------- ------------------------------------------ -------- -------- ---------- ---------- -----------"
    )
    for row in results:
        print(
            f"{_fmt(row['local_rank'], 0):>4} "
            f"{row['stock_code']:<6} "
            f"{str(row.get('stock_name') or '')[:20]:<20} "
            f"{row['state'][:42]:<42} "
            f"{_fmt(row['last_event_age_at_target_sec'], 1):>8} "
            f"{_fmt(row['local_age_at_target_sec'], 1):>8} "
            f"{_fmt(row['event_price'], 0):>10} "
            f"{_fmt(row['local_price'], 0):>10} "
            f"{_fmt(row['kiwoom_price'], 0):>11}"
        )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = RUNTIME_DIR / f"price_history_backtrace_{stamp}.csv"
    json_path = RUNTIME_DIR / f"price_history_backtrace_{stamp}.json"
    if results:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
            writer.writeheader()
            writer.writerows(results)
    else:
        csv_path.write_text("", encoding="utf-8-sig")
    json_path.write_text(
        json.dumps(
            {"summary": output_summary, "rows": results},
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
