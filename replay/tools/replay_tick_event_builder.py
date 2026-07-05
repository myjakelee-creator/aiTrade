from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]

ETF_ETN_PREFIXES = (
    "KODEX",
    "TIGER",
    "ACE",
    "RISE",
    "PLUS",
    "KBSTAR",
    "ARIRANG",
    "HANARO",
    "KOSEF",
    "TIMEFOLIO",
    "SOL",
)

TRADABLE_MASTER_CANDIDATE_RELATIVE_PATHS = (
    "docs/tradable_stock_master.csv",
    "data/tradable_stock_master.csv",
    "tradable_stock_master.csv",
    "data/master/tradable_stock_master.csv",
    "data/stock_master/tradable_stock_master.csv",
    "data/stock/tradable_stock_master.csv",
    "configs/tradable_stock_master.csv",
)


def parse_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text:
        return None
    if text.startswith("+"):
        text = text[1:]
    try:
        return float(text)
    except ValueError:
        return None


def clean_code(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().upper()
    if text.startswith("A"):
        text = text[1:]
    if "_" in text:
        text = text.split("_", 1)[0]
    if not text.isdigit():
        return None
    text = text.zfill(6)
    return text if len(text) == 6 else None


def normalize_code(raw_code: Any) -> tuple[str | None, str | None, str | None]:
    if raw_code in (None, ""):
        return None, None, None
    text = str(raw_code).strip().upper()
    suffix = None
    base = text
    if "_" in text:
        base, suffix = text.split("_", 1)
    code = clean_code(base)
    return code, text, suffix


def parse_dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def rel_ms(ts: str, start_dt: datetime) -> int:
    return int((parse_dt(ts) - start_dt).total_seconds() * 1000)


def second_key(ts: str) -> str:
    return ts[:19]


def minute_key(ts: str) -> str:
    return ts[:16]


def choose_jsonl_member(zip_file: ZipFile, requested_member: str | None) -> str:
    if requested_member:
        return requested_member
    for info in zip_file.infolist():
        if not info.is_dir() and Path(info.filename).suffix.lower() == ".jsonl":
            return info.filename
    raise RuntimeError("No .jsonl member found in zip")


def json_line(raw_line: bytes) -> dict[str, Any] | None:
    try:
        line_text = raw_line.decode("utf-8-sig")
    except UnicodeDecodeError:
        line_text = raw_line.decode("utf-8", errors="replace")
    try:
        payload = json.loads(line_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def skip_master_search_path(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    return bool(
        ".git" in parts
        or "runtime" in parts
        or "patch_backups" in parts
        or "__pycache__" in parts
    )


def append_unique_path(paths: list[Path], seen: set[str], path: Path) -> None:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        resolved = path.absolute()
    key = str(resolved).lower()
    if key in seen:
        return
    seen.add(key)
    paths.append(resolved)


def tradable_master_candidate_paths(requested: Path | None) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()

    if requested is not None:
        if requested.is_absolute():
            append_unique_path(paths, seen, requested)
        else:
            append_unique_path(paths, seen, Path.cwd() / requested)
            append_unique_path(paths, seen, ROOT / requested)

    for rel_path in TRADABLE_MASTER_CANDIDATE_RELATIVE_PATHS:
        append_unique_path(paths, seen, ROOT / rel_path)
        append_unique_path(paths, seen, Path.cwd() / rel_path)

    try:
        discovered = sorted(
            ROOT.rglob("tradable_stock_master.csv"),
            key=lambda item: (len(str(item)), str(item)),
        )
    except OSError:
        discovered = []

    for found in discovered:
        if skip_master_search_path(found):
            continue
        append_unique_path(paths, seen, found)

    return paths


def resolve_tradable_master_path(requested: Path | None) -> Path | None:
    for candidate in tradable_master_candidate_paths(requested):
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def load_tradable_codes(path: Path | None) -> tuple[set[str] | None, str | None]:
    resolved_path = resolve_tradable_master_path(path)
    if resolved_path is None:
        return None, None

    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            codes: set[str] = set()
            with resolved_path.open("r", encoding=encoding, newline="") as handle:
                reader = csv.DictReader(handle)
                for row in reader:
                    code = (
                        row.get("종목코드")
                        or row.get("stock_code")
                        or row.get("code")
                        or row.get("Code")
                        or row.get("종목 코드")
                    )
                    clean = clean_code(code)
                    if clean:
                        codes.add(clean)
            if not codes:
                raise RuntimeError(f"tradable master has no usable stock codes: {resolved_path}")
            return codes, str(resolved_path)
        except Exception as exc:  # noqa: BLE001
            last_error = exc

    raise RuntimeError(f"Failed to read tradable master: {resolved_path} / {last_error}")


def is_builtin_excluded_name(stock_name: Any) -> tuple[bool, str | None]:
    name = str(stock_name or "").strip()
    if not name:
        return False, None

    compact_upper = re.sub(r"\s+", "", name.upper())

    for prefix in ETF_ETN_PREFIXES:
        if compact_upper.startswith(prefix):
            return True, "etf_etn_prefix"

    if "ETN" in compact_upper:
        return True, "etn_name"

    if "스팩" in name:
        return True, "spac"

    if name.endswith("리츠"):
        return True, "reits"

    if re.search(r"(\d+우[BC]?|우[BC]?|우)$", name):
        return True, "preferred_share"

    return False, None


def include_stock(
    stock_code: str | None,
    stock_name: Any,
    tradable_codes: set[str] | None,
    enable_builtin_filter: bool,
) -> tuple[bool, str]:
    if not stock_code:
        return False, "bad_code"

    if tradable_codes is not None and stock_code not in tradable_codes:
        return False, "not_in_tradable_master"

    if enable_builtin_filter:
        excluded, reason = is_builtin_excluded_name(stock_name)
        if excluded:
            return False, reason or "builtin_excluded"

    return True, "ok"


def empty_state(code: str) -> dict[str, Any]:
    return {
        "stock_code": code,
        "stock_code_raw": None,
        "market_suffix": None,
        "stock_name": None,
        "price": None,
        "change_rate": None,
        "open": None,
        "high": None,
        "low": None,
        "close": None,
        "acc_volume": None,
        "acc_trade_value_eok_est": None,
        "minute_key": None,
        "minute_start_acc_volume": None,
        "minute_volume_est": 0.0,
        "minute_trade_value_eok_est": 0.0,
        "execution_strength": None,
        "trade_metric_7": None,
        "ask_remain": None,
        "bid_remain": None,
        "bid_ask_ratio": None,
        "bid_ask_ratio_pct": None,
        "program_net": None,
        "last_ts": None,
        "last_kind": None,
        "event_count": 0,
    }


def compact_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": state.get("stock_code"),
        "stock_code_raw": state.get("stock_code_raw"),
        "market_suffix": state.get("market_suffix"),
        "stock_name": state.get("stock_name"),
        "price": state.get("price"),
        "change_rate": state.get("change_rate"),
        "open": state.get("open"),
        "high": state.get("high"),
        "low": state.get("low"),
        "close": state.get("close"),
        "acc_volume": state.get("acc_volume"),
        "acc_trade_value_eok_est": state.get("acc_trade_value_eok_est"),
        "minute_start_acc_volume": state.get("minute_start_acc_volume"),
        "minute_volume_est": state.get("minute_volume_est"),
        "minute_trade_value_eok_est": state.get("minute_trade_value_eok_est"),
        "execution_strength": state.get("execution_strength"),
        "trade_metric_7": state.get("trade_metric_7"),
        "ask_remain": state.get("ask_remain"),
        "bid_remain": state.get("bid_remain"),
        "bid_ask_ratio": state.get("bid_ask_ratio"),
        "bid_ask_ratio_pct": state.get("bid_ask_ratio_pct"),
        "program_net": state.get("program_net"),
        "last_ts": state.get("last_ts"),
        "last_kind": state.get("last_kind"),
        "event_count": state.get("event_count"),
    }


def update_price_fields(state: dict[str, Any], price: float | None) -> None:
    if price is None:
        return
    state["price"] = price
    state["close"] = price
    if state.get("open") is None:
        state["open"] = price
    state["high"] = price if state.get("high") is None else max(state["high"], price)
    state["low"] = price if state.get("low") is None else min(state["low"], price)


def update_trade_volume(state: dict[str, Any], ts: str, acc_volume: float | None, price: float | None) -> None:
    if acc_volume is None:
        return

    current_minute = minute_key(ts)
    prev_acc = state.get("acc_volume")

    if state.get("minute_key") != current_minute:
        state["minute_key"] = current_minute
        # Use the previous observed cumulative volume as the minute baseline.
        # This avoids over-counting when duplicate/mixed source events arrive within the same minute.
        if prev_acc is not None:
            state["minute_start_acc_volume"] = prev_acc
        else:
            state["minute_start_acc_volume"] = acc_volume
        state["minute_volume_est"] = 0.0
        state["minute_trade_value_eok_est"] = 0.0

    state["acc_volume"] = acc_volume

    if price is not None:
        state["acc_trade_value_eok_est"] = round(acc_volume * price / 100_000_000, 4)

    start_acc = state.get("minute_start_acc_volume")
    if start_acc is None:
        state["minute_start_acc_volume"] = acc_volume
        return

    minute_volume = acc_volume - start_acc
    if minute_volume < 0:
        # Mixed sources can occasionally step backward. Do not accumulate negative noise.
        minute_volume = 0.0

    state["minute_volume_est"] = round(float(minute_volume), 4)
    if price is not None:
        state["minute_trade_value_eok_est"] = round(float(minute_volume) * price / 100_000_000, 4)



def parse_raw_parts(payload: dict[str, Any]) -> tuple[str, list[str]]:
    raw = str(payload.get("raw") or "")
    parts = raw.split("|") if raw else []
    prefix = parts[0] if parts else str(payload.get("kind") or "")
    return prefix, parts


def update_state_from_payload(
    payload: dict[str, Any],
    state_by_code: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    ts = str(payload.get("ts") or "")
    if not ts:
        return None, None

    prefix, parts = parse_raw_parts(payload)
    code: str | None = None
    raw_code: str | None = None
    suffix: str | None = None
    name: str | None = None
    source: str | None = None

    patch_kind = prefix

    if prefix == "TRADE" and len(parts) >= 12:
        source = parts[1]
        code, raw_code, suffix = normalize_code(parts[2])
        name = parts[3]
        price = parse_number(parts[4])
        change_rate = parse_number(parts[5])
        acc_volume = parse_number(parts[6])
        trade_metric_7 = parse_number(parts[7])
        execution_strength = parse_number(parts[11])
    elif prefix == "SYNC" and len(parts) >= 11:
        source = parts[1]
        code, raw_code, suffix = normalize_code(parts[2])
        name = parts[3]
        price = parse_number(parts[4])
        change_rate = parse_number(parts[5])
        acc_volume = parse_number(parts[6])
        trade_metric_7 = None
        execution_strength = None
    elif prefix == "HOGA" and len(parts) >= 4:
        code, raw_code, suffix = normalize_code(parts[1])
        name = None
        price = None
        change_rate = None
        acc_volume = None
        trade_metric_7 = None
        execution_strength = None
    elif prefix == "PROGRAM" and len(parts) >= 3:
        code, raw_code, suffix = normalize_code(parts[1])
        name = None
        price = None
        change_rate = None
        acc_volume = None
        trade_metric_7 = None
        execution_strength = None
    else:
        return None, None

    if code is None:
        return None, None

    state = state_by_code.get(code)
    if state is None:
        state = empty_state(code)
        state_by_code[code] = state

    state["stock_code_raw"] = raw_code or state.get("stock_code_raw")
    state["market_suffix"] = suffix or state.get("market_suffix")
    if name:
        state["stock_name"] = name

    if prefix in {"TRADE", "SYNC"}:
        update_price_fields(state, price)
        if change_rate is not None:
            state["change_rate"] = change_rate
        update_trade_volume(state, ts, acc_volume, price)
        if trade_metric_7 is not None:
            state["trade_metric_7"] = trade_metric_7
        if execution_strength is not None:
            state["execution_strength"] = execution_strength

        if prefix == "SYNC":
            sync_open = parse_number(parts[8]) if len(parts) > 8 else None
            sync_high = parse_number(parts[9]) if len(parts) > 9 else None
            sync_low = parse_number(parts[10]) if len(parts) > 10 else None
            if sync_open is not None:
                state["open"] = sync_open
            if sync_high is not None:
                state["high"] = sync_high
            if sync_low is not None:
                state["low"] = sync_low

    elif prefix == "HOGA":
        # Recorder samples indicate HOGA|code|sell_total|buy_total.
        ask = parse_number(parts[2])
        bid = parse_number(parts[3])
        if ask is not None:
            state["ask_remain"] = ask
        if bid is not None:
            state["bid_remain"] = bid
        if ask not in (None, 0) and bid is not None:
            ratio = bid / ask
            state["bid_ask_ratio"] = round(ratio, 6)
            state["bid_ask_ratio_pct"] = round(ratio * 100, 4)

    elif prefix == "PROGRAM":
        program_net = parse_number(parts[2])
        if program_net is not None:
            state["program_net"] = program_net

    state["last_ts"] = ts
    state["last_kind"] = prefix
    state["event_count"] = int(state.get("event_count") or 0) + 1

    event = {
        "ts": ts,
        "kind": patch_kind,
        "source": source,
        "code": code,
        "raw_code": raw_code,
        "suffix": suffix,
        "stock_name": state.get("stock_name"),
        "raw": payload.get("raw"),
    }

    return event, state


def snapshot_rows(
    state_by_code: dict[str, dict[str, Any]],
    top_n: int,
    tradable_codes: set[str] | None,
    enable_builtin_filter: bool,
) -> list[dict[str, Any]]:
    rows = []
    for state in state_by_code.values():
        if state.get("price") is None:
            continue

        include, _reason = include_stock(
            stock_code=state.get("stock_code"),
            stock_name=state.get("stock_name"),
            tradable_codes=tradable_codes,
            enable_builtin_filter=enable_builtin_filter,
        )
        if not include:
            continue

        rows.append(compact_state(state))

    rows.sort(
        key=lambda row: (
            row.get("acc_trade_value_eok_est") or 0,
            row.get("minute_trade_value_eok_est") or 0,
            row.get("event_count") or 0,
        ),
        reverse=True,
    )

    for rank, row in enumerate(rows[:top_n], start=1):
        row["rank"] = rank

    return rows[:top_n]



def output_prefix(zip_path: Path, start: str, end: str) -> str:
    safe_start = start.replace(":", "").replace("-", "").replace("T", "_")
    safe_end = end.replace(":", "").replace("-", "").replace("T", "_")
    return f"tick_replay_{zip_path.stem}_{safe_start}_{safe_end}"


def build_tick_replay(args: argparse.Namespace) -> dict[str, Any]:
    zip_path = Path(args.zip)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    tradable_codes: set[str] | None = None
    tradable_master_path: str | None = None
    if not args.disable_tradable_master:
        tradable_codes, tradable_master_path = load_tradable_codes(Path(args.tradable_master))

    start_dt = parse_dt(args.start)
    end_dt = parse_dt(args.end)

    prefix = output_prefix(zip_path, args.start, args.end)
    events_path = out_dir / f"{prefix}_events.jsonl"
    snapshot_path = out_dir / f"{prefix}_snapshot.json"
    summary_path = out_dir / f"{prefix}_summary.json"
    latest_sample_path = out_dir / "tick_replay_latest_sample.json"

    state_by_code: dict[str, dict[str, Any]] = {}
    filter_stats = Counter()
    kind_counts = Counter()
    emitted_kind_counts = Counter()
    event_counts_by_code = Counter()
    events_per_second = Counter()
    json_error_count = 0
    scanned_lines = 0
    parsed_events = 0
    emitted_events = 0
    skipped_before = 0
    stopped_after_end = False
    first_event_ts: str | None = None
    last_event_ts: str | None = None
    prev_emit_ts: str | None = None
    snapshot_written = False
    initial_snapshot: dict[str, Any] | None = None
    sample_events: list[dict[str, Any]] = []

    with ZipFile(zip_path) as zf, events_path.open("w", encoding="utf-8", newline="\n") as out:
        member = choose_jsonl_member(zf, args.member)
        with zf.open(member, "r") as handle:
            for raw_line in handle:
                scanned_lines += 1
                payload = json_line(raw_line)
                if payload is None:
                    json_error_count += 1
                    continue

                ts = str(payload.get("ts") or "")
                if not ts:
                    continue

                if ts >= args.end and args.stop_at_end:
                    stopped_after_end = True
                    break

                event, state = update_state_from_payload(payload, state_by_code)
                if event is None or state is None:
                    continue

                parsed_events += 1
                kind_counts[event["kind"]] += 1

                code = event["code"]
                include, reason = include_stock(
                    stock_code=code,
                    stock_name=state.get("stock_name"),
                    tradable_codes=tradable_codes,
                    enable_builtin_filter=not args.disable_builtin_filter,
                )
                if not include:
                    filter_stats[f"excluded_{reason}"] += 1
                    continue

                filter_stats["included_seen"] += 1

                if ts < args.start:
                    skipped_before += 1
                    continue

                if not snapshot_written:
                    rows = snapshot_rows(
                        state_by_code,
                        top_n=args.top_n,
                        tradable_codes=tradable_codes,
                        enable_builtin_filter=not args.disable_builtin_filter,
                    )
                    initial_snapshot = {
                        "session_id": prefix,
                        "mode": "tick_replay",
                        "start": args.start,
                        "end": args.end,
                        "snapshot_ts": args.start,
                        "row_count": len(rows),
                        "rows": rows,
                        "safety": {
                            "replay_only": True,
                            "kiwoom_connection": False,
                            "order_enabled": False,
                            "live_api_reuse": False,
                        },
                    }
                    snapshot_path.write_text(json.dumps(initial_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                    snapshot_written = True

                patch = compact_state(state)
                event_dt = parse_dt(ts)
                delta_ms = None
                if prev_emit_ts is not None:
                    delta_ms = int((event_dt - parse_dt(prev_emit_ts)).total_seconds() * 1000)

                emitted = {
                    "session_id": prefix,
                    "i": emitted_events,
                    "source_line": scanned_lines,
                    "ts": ts,
                    "time_text": ts[11:23],
                    "rel_ms": rel_ms(ts, start_dt),
                    "delta_ms": delta_ms,
                    "kind": event["kind"],
                    "source": event.get("source"),
                    "code": code,
                    "stock_name": state.get("stock_name"),
                    "patch": patch,
                    "raw": event.get("raw"),
                }

                out.write(json.dumps(emitted, ensure_ascii=False, separators=(",", ":")) + "\n")

                if len(sample_events) < args.sample_count:
                    sample_events.append(emitted)

                emitted_events += 1
                emitted_kind_counts[event["kind"]] += 1
                event_counts_by_code[code] += 1
                events_per_second[second_key(ts)] += 1
                first_event_ts = first_event_ts or ts
                last_event_ts = ts
                prev_emit_ts = ts

                if args.max_events and emitted_events >= args.max_events:
                    break

                if args.progress and emitted_events and emitted_events % args.progress == 0:
                    print(
                        f"progress emitted={emitted_events:,} scanned={scanned_lines:,} ts={ts}",
                        flush=True,
                    )

    if not snapshot_written:
        rows = snapshot_rows(
            state_by_code,
            top_n=args.top_n,
            tradable_codes=tradable_codes,
            enable_builtin_filter=not args.disable_builtin_filter,
        )
        initial_snapshot = {
            "session_id": prefix,
            "mode": "tick_replay",
            "start": args.start,
            "end": args.end,
            "snapshot_ts": args.start,
            "row_count": len(rows),
            "rows": rows,
            "safety": {
                "replay_only": True,
                "kiwoom_connection": False,
                "order_enabled": False,
                "live_api_reuse": False,
            },
        }
        snapshot_path.write_text(json.dumps(initial_snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    total_window_ms = int((end_dt - start_dt).total_seconds() * 1000)
    top_seconds = [
        {"second": second, "events": count}
        for second, count in events_per_second.most_common(20)
    ]

    summary = {
        "session_id": prefix,
        "zip_path": str(zip_path),
        "member": args.member,
        "start": args.start,
        "end": args.end,
        "window_ms": total_window_ms,
        "scanned_lines": scanned_lines,
        "json_error_count": json_error_count,
        "parsed_events": parsed_events,
        "skipped_before_window": skipped_before,
        "emitted_events": emitted_events,
        "stopped_after_end": stopped_after_end,
        "first_event_ts": first_event_ts,
        "last_event_ts": last_event_ts,
        "kind_counts_seen": dict(kind_counts.most_common()),
        "kind_counts_emitted": dict(emitted_kind_counts.most_common()),
        "top_codes_by_event_count": event_counts_by_code.most_common(30),
        "events_per_second_max": max(events_per_second.values()) if events_per_second else 0,
        "events_per_second_avg": round(emitted_events / max(1, total_window_ms / 1000), 4),
        "top_event_seconds": top_seconds,
        "tradable_filter": {
            "enabled": True,
            "tradable_master_path": tradable_master_path,
            "tradable_master_code_count": len(tradable_codes) if tradable_codes is not None else None,
            "builtin_filter_enabled": not args.disable_builtin_filter,
            "filter_stats": dict(filter_stats),
            "excluded_total": sum(value for key, value in filter_stats.items() if key.startswith("excluded_")),
        },
        "outputs": {
            "events_jsonl": str(events_path),
            "snapshot_json": str(snapshot_path),
            "summary_json": str(summary_path),
            "latest_sample_json": str(latest_sample_path),
        },
        "safety": {
            "replay_only": True,
            "kiwoom_connection": False,
            "order_enabled": False,
            "live_api_reuse": False,
        },
        "notes": {
            "mode": "tick patch replay input",
            "board_strategy": "initial snapshot + per-code tick patch",
            "trade_metric_7": "Unresolved diagnostic metric. Not a scoring input.",
        },
    }

    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_sample_path.write_text(json.dumps({
        "summary": summary,
        "sample_events": sample_events,
        "snapshot_sample": (initial_snapshot or {}).get("rows", [])[:10],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build tick-level replay events for StockBoard stress/replay tests.")
    parser.add_argument("--zip", default="data/runtime/replay_inbox/live_raw_20260616.zip")
    parser.add_argument("--member", default=None)
    parser.add_argument("--start", default="2026-06-16T08:59:00")
    parser.add_argument("--end", default="2026-06-16T09:10:00")
    parser.add_argument("--out-dir", default="data/runtime/replay_output")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--tradable-master", default="docs/tradable_stock_master.csv")
    parser.add_argument("--disable-tradable-master", action="store_true")
    parser.add_argument("--disable-builtin-filter", action="store_true")
    parser.add_argument("--max-events", type=int, default=0)
    parser.add_argument("--progress", type=int, default=50000)
    parser.add_argument("--sample-count", type=int, default=10)
    parser.add_argument("--stop-at-end", action="store_true", default=True)
    args = parser.parse_args()

    summary = build_tick_replay(args)

    print("REPLAY_TICK_EVENTS_OK")
    print("session_id:", summary["session_id"])
    print("window:", summary["start"], "=>", summary["end"])
    print("scanned_lines:", f"{summary['scanned_lines']:,}")
    print("parsed_events:", f"{summary['parsed_events']:,}")
    print("emitted_events:", f"{summary['emitted_events']:,}")
    print("kind_counts_emitted:", summary["kind_counts_emitted"])
    print("events_per_second_max:", summary["events_per_second_max"])
    print("events_per_second_avg:", summary["events_per_second_avg"])
    print("top_event_seconds:", summary["top_event_seconds"][:10])
    print("tradable_filter:", summary["tradable_filter"])
    print("events_jsonl:", summary["outputs"]["events_jsonl"])
    print("snapshot_json:", summary["outputs"]["snapshot_json"])
    print("summary_json:", summary["outputs"]["summary_json"])
    print("latest_sample_json:", summary["outputs"]["latest_sample_json"])
    print("safety:", summary["safety"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())