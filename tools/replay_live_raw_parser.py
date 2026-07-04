from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zipfile import ZipFile


def parse_number(value: Any) -> float | None:
    if value is None:
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


def normalize_code(raw_code: Any) -> tuple[str | None, str | None, str | None]:
    if raw_code in (None, ""):
        return None, None, None
    text = str(raw_code).strip().upper()
    suffix = None
    base = text
    if "_" in text:
        base, suffix = text.split("_", 1)
    if base.startswith("A"):
        base = base[1:]
    if base.isdigit():
        base = base.zfill(6)
    if not base or not base.isdigit() or len(base) != 6:
        return None, text, suffix
    return base, text, suffix


def minute_key(ts_text: str) -> str:
    return ts_text[:16]


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


def delta_or_none(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    delta = current - previous
    if delta < 0:
        return None
    return delta


@dataclass
class MinuteAgg:
    minute_kst: str
    stock_code: str
    stock_code_raw: str | None = None
    market_suffix: str | None = None
    stock_name: str | None = None
    first_ts: str | None = None
    last_ts: str | None = None

    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    last_change_rate: float | None = None

    trade_tick_count: int = 0
    sync_count: int = 0
    hoga_count: int = 0
    program_count: int = 0

    # Raw retained fields
    trade_f6_last: float | None = None
    trade_f6_max: float | None = None
    trade_f7_last: float | None = None
    trade_f7_sum: float = 0.0
    trade_f11_last: float | None = None

    # Semantic aliases / diagnostic fields
    acc_volume: float | None = None
    acc_volume_max: float | None = None
    execution_strength: float | None = None

    # TRADE[7] is intentionally unresolved. Keep OHLC-style movement for replay diagnosis.
    trade_metric_7_open: float | None = None
    trade_metric_7_high: float | None = None
    trade_metric_7_low: float | None = None
    trade_metric_7_close: float | None = None
    trade_metric_7_sum_debug: float = 0.0

    sync_value6_last: float | None = None
    sync_open_last: float | None = None
    sync_high_last: float | None = None
    sync_low_last: float | None = None

    ask_remain_last: float | None = None
    bid_remain_last: float | None = None
    bid_ask_ratio_last: float | None = None
    bid_ask_ratio_pct_last: float | None = None

    program_net_last: float | None = None

    def update_identity(self, ts: str, raw_code: str | None, suffix: str | None, name: str | None) -> None:
        if self.first_ts is None:
            self.first_ts = ts
        self.last_ts = ts
        if raw_code:
            self.stock_code_raw = raw_code
        if suffix:
            self.market_suffix = suffix
        if name:
            self.stock_name = name

    def update_price(self, price: float | None) -> None:
        if price is None:
            return
        if self.open is None:
            self.open = price
        self.close = price
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)

    def update_metric_7(self, value: float | None) -> None:
        if value is None:
            return
        if self.trade_metric_7_open is None:
            self.trade_metric_7_open = value
        self.trade_metric_7_close = value
        self.trade_metric_7_high = value if self.trade_metric_7_high is None else max(self.trade_metric_7_high, value)
        self.trade_metric_7_low = value if self.trade_metric_7_low is None else min(self.trade_metric_7_low, value)
        self.trade_metric_7_sum_debug += value

    def update_trade(self, ts: str, raw_code: str | None, suffix: str | None, name: str | None, parts: list[str]) -> None:
        self.update_identity(ts, raw_code, suffix, name)
        self.trade_tick_count += 1

        price = parse_number(parts[4]) if len(parts) > 4 else None
        change_rate = parse_number(parts[5]) if len(parts) > 5 else None
        f6 = parse_number(parts[6]) if len(parts) > 6 else None
        f7 = parse_number(parts[7]) if len(parts) > 7 else None
        f11 = parse_number(parts[11]) if len(parts) > 11 else None

        self.update_price(price)
        if change_rate is not None:
            self.last_change_rate = change_rate

        if f6 is not None:
            self.trade_f6_last = f6
            self.trade_f6_max = f6 if self.trade_f6_max is None else max(self.trade_f6_max, f6)
            self.acc_volume = f6
            self.acc_volume_max = self.trade_f6_max

        if f7 is not None:
            self.trade_f7_last = f7
            self.trade_f7_sum += f7
            self.update_metric_7(f7)

        if f11 is not None:
            self.trade_f11_last = f11
            self.execution_strength = f11

    def update_sync(self, ts: str, raw_code: str | None, suffix: str | None, name: str | None, parts: list[str]) -> None:
        self.update_identity(ts, raw_code, suffix, name)
        self.sync_count += 1

        price = parse_number(parts[4]) if len(parts) > 4 else None
        change_rate = parse_number(parts[5]) if len(parts) > 5 else None
        value6 = parse_number(parts[6]) if len(parts) > 6 else None
        sync_open = parse_number(parts[8]) if len(parts) > 8 else None
        sync_high = parse_number(parts[9]) if len(parts) > 9 else None
        sync_low = parse_number(parts[10]) if len(parts) > 10 else None

        self.update_price(price)
        if change_rate is not None:
            self.last_change_rate = change_rate
        if value6 is not None:
            self.sync_value6_last = value6
            if self.acc_volume is None:
                self.acc_volume = value6
        if sync_open is not None:
            self.sync_open_last = sync_open
        if sync_high is not None:
            self.sync_high_last = sync_high
        if sync_low is not None:
            self.sync_low_last = sync_low

    def update_hoga(self, ts: str, raw_code: str | None, suffix: str | None, parts: list[str]) -> None:
        self.update_identity(ts, raw_code, suffix, None)
        self.hoga_count += 1

        # Current recorder log indicates: HOGA|code|sell_total|buy_total.
        ask = parse_number(parts[2]) if len(parts) > 2 else None
        bid = parse_number(parts[3]) if len(parts) > 3 else None

        if ask is not None:
            self.ask_remain_last = ask
        if bid is not None:
            self.bid_remain_last = bid
        if ask not in (None, 0) and bid is not None:
            ratio = bid / ask
            self.bid_ask_ratio_last = ratio
            self.bid_ask_ratio_pct_last = ratio * 100

    def update_program(self, ts: str, raw_code: str | None, suffix: str | None, parts: list[str]) -> None:
        self.update_identity(ts, raw_code, suffix, None)
        self.program_count += 1

        value = parse_number(parts[2]) if len(parts) > 2 else None
        if value is not None:
            self.program_net_last = value

    def row(self) -> dict[str, Any]:
        metric_7_delta_in_min = None
        if self.trade_metric_7_open is not None and self.trade_metric_7_close is not None:
            metric_7_delta_in_min = self.trade_metric_7_close - self.trade_metric_7_open

        return {
            "minute_kst": self.minute_kst,
            "stock_code": self.stock_code,
            "stock_code_raw": self.stock_code_raw,
            "market_suffix": self.market_suffix,
            "stock_name": self.stock_name,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "last_change_rate": self.last_change_rate,
            "trade_tick_count": self.trade_tick_count,
            "sync_count": self.sync_count,
            "hoga_count": self.hoga_count,
            "program_count": self.program_count,

            # Raw retained fields
            "trade_f6_last": self.trade_f6_last,
            "trade_f6_max": self.trade_f6_max,
            "trade_f7_last": self.trade_f7_last,
            "trade_f7_sum": round(self.trade_f7_sum, 4),
            "trade_f11_last": self.trade_f11_last,

            # Semantic fields
            "acc_volume": self.acc_volume,
            "acc_volume_max": self.acc_volume_max,
            "minute_volume_est": None,
            "minute_trade_value_eok_est": None,
            "execution_strength": self.execution_strength,

            # Neutral diagnostic fields for TRADE[7]
            "trade_metric_7_open": self.trade_metric_7_open,
            "trade_metric_7_high": self.trade_metric_7_high,
            "trade_metric_7_low": self.trade_metric_7_low,
            "trade_metric_7_close": self.trade_metric_7_close,
            "trade_metric_7_delta_in_min": metric_7_delta_in_min,
            "trade_metric_7_delta_from_prev_min": None,
            "trade_metric_7_sum_debug": round(self.trade_metric_7_sum_debug, 4),

            # SYNC / HOGA / PROGRAM
            "sync_value6_last": self.sync_value6_last,
            "sync_open_last": self.sync_open_last,
            "sync_high_last": self.sync_high_last,
            "sync_low_last": self.sync_low_last,
            "ask_remain_last": self.ask_remain_last,
            "bid_remain_last": self.bid_remain_last,
            "bid_ask_ratio_last": self.bid_ask_ratio_last,
            "bid_ask_ratio_pct_last": self.bid_ask_ratio_pct_last,
            "bid_ask_ratio": self.bid_ask_ratio_last,
            "program_net_last": self.program_net_last,
            "program_net": self.program_net_last,
        }


def choose_jsonl_member(zip_file: ZipFile, requested_member: str | None) -> str:
    if requested_member:
        return requested_member
    for info in zip_file.infolist():
        if not info.is_dir() and Path(info.filename).suffix.lower() == ".jsonl":
            return info.filename
    raise RuntimeError("No .jsonl member found in zip")


def output_prefix(zip_path: Path, start: str, end: str) -> str:
    safe_start = start.replace(":", "").replace("-", "").replace("T", "_")
    safe_end = end.replace(":", "").replace("-", "").replace("T", "_")
    return f"{zip_path.stem}_{safe_start}_{safe_end}"


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def add_derived_minute_fields(rows: list[dict[str, Any]]) -> None:
    prev_by_code: dict[str, dict[str, Any]] = {}

    for row in sorted(rows, key=lambda item: (item.get("stock_code") or "", item.get("minute_kst") or "")):
        code = row.get("stock_code")
        prev = prev_by_code.get(code)

        acc_volume = parse_number(row.get("acc_volume"))
        prev_acc_volume = parse_number(prev.get("acc_volume")) if prev else None
        minute_volume = delta_or_none(acc_volume, prev_acc_volume)

        close = parse_number(row.get("close"))
        if minute_volume is not None:
            row["minute_volume_est"] = minute_volume
            if close is not None:
                row["minute_trade_value_eok_est"] = round(minute_volume * close / 100_000_000, 4)

        metric_close = parse_number(row.get("trade_metric_7_close"))
        prev_metric_close = parse_number(prev.get("trade_metric_7_close")) if prev else None
        if metric_close is not None and prev_metric_close is not None:
            row["trade_metric_7_delta_from_prev_min"] = metric_close - prev_metric_close

        prev_by_code[str(code)] = row


def parse_zip(args: argparse.Namespace) -> dict[str, Any]:
    zip_path = Path(args.zip)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    aggregates: dict[tuple[str, str], MinuteAgg] = {}
    kind_counts = Counter()
    prefix_counts = Counter()
    suffix_counts = Counter()
    raw_len_counts = Counter()
    code_counts = Counter()
    json_error_count = 0
    scanned_lines = 0
    accepted_lines = 0
    first_ts = None
    last_ts = None

    with ZipFile(zip_path) as zf:
        member = choose_jsonl_member(zf, args.member)
        with zf.open(member, "r") as handle:
            for raw_line in handle:
                scanned_lines += 1
                if args.max_lines and scanned_lines > args.max_lines:
                    break

                payload = json_line(raw_line)
                if payload is None:
                    json_error_count += 1
                    continue

                ts = str(payload.get("ts") or "")
                if not ts:
                    continue

                if first_ts is None:
                    first_ts = ts
                last_ts = ts

                if ts < args.start:
                    continue
                if ts >= args.end:
                    if args.stop_at_end:
                        break
                    continue

                raw = str(payload.get("raw") or "")
                parts = raw.split("|") if raw else []
                if not parts:
                    continue

                kind = str(payload.get("kind") or "")
                prefix = parts[0]
                kind_counts[kind] += 1
                prefix_counts[prefix] += 1
                raw_len_counts[len(parts)] += 1
                accepted_lines += 1

                code = None
                raw_code = None
                suffix = None
                stock_name = None

                if prefix in {"TRADE", "SYNC"} and len(parts) >= 4:
                    code, raw_code, suffix = normalize_code(parts[2])
                    stock_name = parts[3]
                elif prefix == "HOGA" and len(parts) >= 2:
                    code, raw_code, suffix = normalize_code(parts[1])
                elif prefix == "PROGRAM" and len(parts) >= 2:
                    code, raw_code, suffix = normalize_code(parts[1])
                else:
                    continue

                if code is None:
                    continue

                suffix_counts[suffix or "<none>"] += 1
                code_counts[code] += 1

                minute = minute_key(ts)
                key = (minute, code)
                agg = aggregates.get(key)
                if agg is None:
                    agg = MinuteAgg(minute_kst=minute, stock_code=code)
                    aggregates[key] = agg

                if prefix == "TRADE":
                    agg.update_trade(ts, raw_code, suffix, stock_name, parts)
                elif prefix == "SYNC":
                    agg.update_sync(ts, raw_code, suffix, stock_name, parts)
                elif prefix == "HOGA":
                    agg.update_hoga(ts, raw_code, suffix, parts)
                elif prefix == "PROGRAM":
                    agg.update_program(ts, raw_code, suffix, parts)

                if args.progress and accepted_lines % args.progress == 0:
                    print(f"progress accepted={accepted_lines:,} scanned={scanned_lines:,} ts={ts}", flush=True)

    rows = [agg.row() for agg in aggregates.values()]
    rows.sort(key=lambda item: (item["minute_kst"], item["stock_code"]))
    add_derived_minute_fields(rows)

    top_rows = []
    by_minute: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_minute[row["minute_kst"]].append(row)

    for minute, minute_rows in sorted(by_minute.items()):
        ranked = sorted(
            minute_rows,
            key=lambda row: (
                parse_number(row.get("minute_trade_value_eok_est")) or 0,
                parse_number(row.get("trade_tick_count")) or 0,
                abs(parse_number(row.get("trade_metric_7_delta_in_min")) or 0),
                parse_number(row.get("acc_volume")) or 0,
            ),
            reverse=True,
        )[: args.top_n]
        for rank, row in enumerate(ranked, start=1):
            next_row = dict(row)
            next_row["activity_rank"] = rank
            top_rows.append(next_row)

    prefix = output_prefix(zip_path, args.start, args.end)
    agg_csv = out_dir / f"{prefix}_minute_agg.csv"
    top_csv = out_dir / f"{prefix}_minute_top{args.top_n}.csv"
    summary_json = out_dir / f"{prefix}_summary.json"

    fieldnames = list(MinuteAgg(minute_kst="", stock_code="").row().keys())
    write_csv(agg_csv, rows, fieldnames)

    top_fieldnames = ["activity_rank"] + fieldnames
    write_csv(top_csv, top_rows, top_fieldnames)

    summary = {
        "zip_path": str(zip_path),
        "member": member,
        "start": args.start,
        "end": args.end,
        "max_lines": args.max_lines,
        "stop_at_end": args.stop_at_end,
        "scanned_lines": scanned_lines,
        "accepted_lines": accepted_lines,
        "json_error_count": json_error_count,
        "first_ts_seen": first_ts,
        "last_ts_seen": last_ts,
        "aggregate_rows": len(rows),
        "top_rows": len(top_rows),
        "minute_count": len(by_minute),
        "kind_counts": dict(kind_counts.most_common()),
        "prefix_counts": dict(prefix_counts.most_common()),
        "raw_len_counts": dict(raw_len_counts.most_common()),
        "suffix_counts": dict(suffix_counts.most_common()),
        "top_codes": code_counts.most_common(30),
        "semantic_notes": {
            "acc_volume": "TRADE[6], likely accumulated volume",
            "execution_strength": "TRADE[11], likely execution strength",
            "trade_metric_7": "TRADE[7], unresolved signed diagnostic metric; do not use for candidate scoring yet",
            "hoga_mapping": "HOGA[2]=ask/sell remain, HOGA[3]=bid/buy remain based on recorder MSG samples",
        },
        "outputs": {
            "minute_agg_csv": str(agg_csv),
            "minute_top_csv": str(top_csv),
            "summary_json": str(summary_json),
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay parser for StockBoard live_raw JSONL zip.")
    parser.add_argument("--zip", required=True, help="Path to live_raw zip file.")
    parser.add_argument("--member", default=None, help="Optional JSONL member name inside zip.")
    parser.add_argument("--start", required=True, help="Inclusive ISO timestamp, e.g. 2026-06-16T08:50:00")
    parser.add_argument("--end", required=True, help="Exclusive ISO timestamp, e.g. 2026-06-16T09:30:00")
    parser.add_argument("--out-dir", default="data/runtime/replay_output")
    parser.add_argument("--top-n", type=int, default=30)
    parser.add_argument("--max-lines", type=int, default=0)
    parser.add_argument("--progress", type=int, default=200000)
    parser.add_argument("--stop-at-end", action="store_true", default=True)
    args = parser.parse_args()

    summary = parse_zip(args)

    print("REPLAY_PARSE_OK")
    print("zip:", summary["zip_path"])
    print("member:", summary["member"])
    print("window:", summary["start"], "=>", summary["end"])
    print("scanned_lines:", f"{summary['scanned_lines']:,}")
    print("accepted_lines:", f"{summary['accepted_lines']:,}")
    print("aggregate_rows:", f"{summary['aggregate_rows']:,}")
    print("minute_count:", summary["minute_count"])
    print("kind_counts:", summary["kind_counts"])
    print("prefix_counts:", summary["prefix_counts"])
    print("suffix_counts:", summary["suffix_counts"])
    print("top_codes:", summary["top_codes"][:10])
    print("minute_agg_csv:", summary["outputs"]["minute_agg_csv"])
    print("minute_top_csv:", summary["outputs"]["minute_top_csv"])
    print("summary_json:", summary["outputs"]["summary_json"])
    print("semantic_notes:", summary["semantic_notes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())