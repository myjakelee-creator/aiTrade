from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


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


def parse_int(value: Any) -> int | None:
    number = parse_number(value)
    if number is None:
        return None
    return int(number)


def clean_code(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.startswith("A"):
        text = text[1:]
    if "_" in text:
        text = text.split("_", 1)[0]
    if not text.isdigit():
        return None
    text = text.zfill(6)
    return text if len(text) == 6 else None


def latest_file(out_dir: Path, pattern: str, exclude: tuple[str, ...] = ()) -> Path:
    files = sorted(out_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    files = [path for path in files if not any(token in path.name for token in exclude)]
    if not files:
        raise RuntimeError(f"No file found: pattern={pattern}, exclude={exclude}, out_dir={out_dir}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def row_sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
    return (
        parse_number(row.get("minute_trade_value_eok_est")) or 0,
        parse_number(row.get("trade_tick_count")) or 0,
        parse_number(row.get("acc_volume")) or 0,
    )


def candidate_sort_key(row: dict[str, Any], rank_field: str) -> tuple[int, float]:
    rank = parse_int(row.get(rank_field)) or 999999
    score = parse_number(row.get("stable_score") or row.get("candidate_score")) or 0
    return rank, -score


def build_candidate_record(row: dict[str, Any], *, stable: bool) -> dict[str, Any]:
    rank_field = "stable_candidate_rank" if stable else "candidate_rank"
    score_field = "stable_score" if stable else "candidate_score"
    grade_field = "stable_grade_text" if stable else "candidate_grade_text"

    return {
        "mode": "stable" if stable else "raw",
        "model_id": row.get("requested_model_id"),
        "resolved_model_id": row.get("resolved_model_id"),
        "rank": parse_int(row.get(rank_field)),
        "stock_code": clean_code(row.get("stock_code")),
        "stock_name": row.get("stock_name"),
        "score": parse_number(row.get(score_field)),
        "grade_text": row.get(grade_field),
        "candidate_score": parse_number(row.get("candidate_score")),
        "candidate_grade_text": row.get("candidate_grade_text"),
        "stable_score": parse_number(row.get("stable_score")),
        "stable_score_before_gate": parse_number(row.get("stable_score_before_gate")),
        "stable_band": row.get("stable_band"),
        "stable_gate_cap_reason": row.get("stable_gate_cap_reason"),
        "entry_score": parse_number(row.get("entry_score")),
        "confirmation_score": parse_number(row.get("confirmation_score")),
        "focus_score": parse_number(row.get("focus_score")),
        "price": parse_number(row.get("price")),
        "change_rate": parse_number(row.get("change_rate")),
        "trade_value_eok": parse_number(row.get("trade_value_eok")),
        "minute_volume_est": parse_number(row.get("minute_volume_est")),
        "execution_strength": parse_number(row.get("execution_strength")),
        "bid_ask_ratio": parse_number(row.get("bid_ask_ratio")),
        "program_net": parse_number(row.get("program_net")),
        "trade_metric_7_close": parse_number(row.get("trade_metric_7_close")),
        "trade_metric_7_delta_in_min": parse_number(row.get("trade_metric_7_delta_in_min")),
        "trade_metric_7_delta_from_prev_min": parse_number(row.get("trade_metric_7_delta_from_prev_min")),
    }


def build_candidate_groups(rows: list[dict[str, Any]], *, stable: bool) -> dict[str, dict[str, list[dict[str, Any]]]]:
    rank_field = "stable_candidate_rank" if stable else "candidate_rank"
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        minute = str(row.get("replay_minute") or "")
        model_id = str(row.get("requested_model_id") or "")
        code = clean_code(row.get("stock_code"))
        if not minute or not model_id or not code:
            continue
        record = build_candidate_record(row, stable=stable)
        groups[minute][model_id].append(record)

    for model_groups in groups.values():
        for model_id, candidates in model_groups.items():
            candidates.sort(key=lambda item: (item.get("rank") or 999999, -(item.get("score") or 0)))
            model_groups[model_id] = candidates

    return groups


def normalize_board_row(source: dict[str, Any], rank: int, prev_rank: int | None) -> dict[str, Any]:
    stock_code = clean_code(source.get("stock_code"))
    price = parse_number(source.get("close"))
    change_rate = parse_number(source.get("last_change_rate"))
    minute_trade_value = parse_number(source.get("minute_trade_value_eok_est"))
    execution_strength = parse_number(source.get("execution_strength"))

    ohlc = {
        "open": parse_number(source.get("open")),
        "high": parse_number(source.get("high")),
        "low": parse_number(source.get("low")),
        "close": price,
    }

    return {
        "rank": rank,
        "prev_rank": prev_rank,
        "rank_diff": (prev_rank - rank) if prev_rank is not None else None,
        "stock_code": stock_code,
        "stock_name": source.get("stock_name"),
        "price": price,
        "current_price": price,
        "display_price": price,
        "realtime_price": price,
        "change_rate": change_rate,
        "display_change_rate": change_rate,
        "realtime_change_rate": change_rate,
        "trade_value_eok": minute_trade_value,
        "one_min_trade_value_eok": minute_trade_value,
        "amount_eok": minute_trade_value,
        "minute_volume_est": parse_number(source.get("minute_volume_est")),
        "acc_volume": parse_number(source.get("acc_volume")),
        "trade_tick_count": parse_int(source.get("trade_tick_count")),
        "execution_strength": execution_strength,
        "strength_1m": execution_strength,
        "strength_day": execution_strength,
        "bid_ask_ratio": parse_number(source.get("bid_ask_ratio")),
        "bid_ask_ratio_pct_last": parse_number(source.get("bid_ask_ratio_pct_last")),
        "program_net": parse_number(source.get("program_net")),
        "program_net_last": parse_number(source.get("program_net_last")),
        "ohlc": ohlc,
        "display_ohlc": ohlc,
        "realtime_ohlc": ohlc,
        "trade_metric_7_open": parse_number(source.get("trade_metric_7_open")),
        "trade_metric_7_high": parse_number(source.get("trade_metric_7_high")),
        "trade_metric_7_low": parse_number(source.get("trade_metric_7_low")),
        "trade_metric_7_close": parse_number(source.get("trade_metric_7_close")),
        "trade_metric_7_delta_in_min": parse_number(source.get("trade_metric_7_delta_in_min")),
        "trade_metric_7_delta_from_prev_min": parse_number(source.get("trade_metric_7_delta_from_prev_min")),
        "replay_only": True,
    }


def build_board_rows_by_minute(
    minute_agg_rows: list[dict[str, Any]],
    *,
    top_n: int,
    tradable_codes: set[str] | None,
    enable_builtin_filter: bool,
) -> tuple[dict[str, list[dict[str, Any]]], Counter]:
    source_by_minute: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in minute_agg_rows:
        minute = str(row.get("minute_kst") or "")
        if minute:
            source_by_minute[minute].append(row)

    filter_stats: Counter = Counter()
    prev_rank_by_code: dict[str, int] = {}
    board_by_minute: dict[str, list[dict[str, Any]]] = {}

    for minute in sorted(source_by_minute):
        ranked = sorted(source_by_minute[minute], key=row_sort_key, reverse=True)
        rows: list[dict[str, Any]] = []
        current_rank_by_code: dict[str, int] = {}

        for source in ranked:
            filter_stats["source_rows_seen"] += 1
            stock_code = clean_code(source.get("stock_code"))
            include, reason = include_stock(
                stock_code=stock_code,
                stock_name=source.get("stock_name"),
                tradable_codes=tradable_codes,
                enable_builtin_filter=enable_builtin_filter,
            )
            if not include:
                filter_stats[f"excluded_{reason}"] += 1
                continue

            assert stock_code is not None
            rank = len(rows) + 1
            prev_rank = prev_rank_by_code.get(stock_code)
            normalized = normalize_board_row(source, rank=rank, prev_rank=prev_rank)
            rows.append(normalized)
            current_rank_by_code[stock_code] = rank
            filter_stats["included_rows"] += 1

            if len(rows) >= top_n:
                break

        prev_rank_by_code = current_rank_by_code
        board_by_minute[minute] = rows

    return board_by_minute, filter_stats


def annotate_candidate_flags(
    rows: list[dict[str, Any]],
    raw_by_model: dict[str, list[dict[str, Any]]],
    stable_by_model: dict[str, list[dict[str, Any]]],
) -> None:
    raw_rank_by_code: dict[str, dict[str, int]] = defaultdict(dict)
    stable_rank_by_code: dict[str, dict[str, int]] = defaultdict(dict)

    for model_id, candidates in raw_by_model.items():
        for candidate in candidates:
            code = candidate.get("stock_code")
            rank = candidate.get("rank")
            if code and rank is not None:
                raw_rank_by_code[str(code)][model_id] = int(rank)

    for model_id, candidates in stable_by_model.items():
        for candidate in candidates:
            code = candidate.get("stock_code")
            rank = candidate.get("rank")
            if code and rank is not None:
                stable_rank_by_code[str(code)][model_id] = int(rank)

    for row in rows:
        code = str(row.get("stock_code") or "")
        raw_models = dict(raw_rank_by_code.get(code, {}))
        stable_models = dict(stable_rank_by_code.get(code, {}))
        row["is_raw_candidate"] = bool(raw_models)
        row["is_stable_candidate"] = bool(stable_models)
        row["raw_candidate_rank_by_model"] = raw_models
        row["stable_candidate_rank_by_model"] = stable_models


def build_frames(
    *,
    minute_agg_csv: Path,
    raw_candidate_csv: Path,
    stable_candidate_csv: Path,
    out_dir: Path,
    top_n: int,
    default_model_id: str,
    session_id: str | None,
    tradable_codes: set[str] | None,
    tradable_master_path: str | None,
    enable_builtin_filter: bool,
) -> dict[str, Any]:
    minute_agg_rows = read_csv(minute_agg_csv)
    raw_candidate_rows = read_csv(raw_candidate_csv)
    stable_candidate_rows = read_csv(stable_candidate_csv)

    raw_groups = build_candidate_groups(raw_candidate_rows, stable=False)
    stable_groups = build_candidate_groups(stable_candidate_rows, stable=True)

    board_by_minute, filter_stats = build_board_rows_by_minute(
        minute_agg_rows,
        top_n=top_n,
        tradable_codes=tradable_codes,
        enable_builtin_filter=enable_builtin_filter,
    )

    minutes = sorted(set(board_by_minute) | set(raw_groups) | set(stable_groups))
    if not minutes:
        raise RuntimeError("No replay minutes found.")

    prefix = minute_agg_csv.stem.replace("_minute_agg", "")
    session = session_id or prefix

    frames_path = out_dir / f"{prefix}_stockboard_frames.jsonl"
    index_path = out_dir / f"{prefix}_stockboard_frame_index.json"
    summary_path = out_dir / f"{prefix}_stockboard_frame_summary.json"
    latest_sample_path = out_dir / "replay_stockboard_frame_latest_sample.json"

    index_minutes: list[dict[str, Any]] = []
    model_ids = sorted({
        *{model_id for minute in raw_groups.values() for model_id in minute.keys()},
        *{model_id for minute in stable_groups.values() for model_id in minute.keys()},
    })

    first_frame: dict[str, Any] | None = None
    last_frame: dict[str, Any] | None = None

    with frames_path.open("w", encoding="utf-8", newline="\n") as handle:
        for idx, minute in enumerate(minutes):
            rows = board_by_minute.get(minute, [])
            raw_by_model = raw_groups.get(minute, {})
            stable_by_model = stable_groups.get(minute, {})

            annotate_candidate_flags(rows, raw_by_model, stable_by_model)

            frame = {
                "session_id": session,
                "minute": minute,
                "minute_index": idx,
                "minute_count": len(minutes),
                "replay_only": True,
                "source": {
                    "minute_agg_csv": str(minute_agg_csv),
                    "raw_candidate_csv": str(raw_candidate_csv),
                    "stable_candidate_csv": str(stable_candidate_csv),
                    "tradable_master_path": tradable_master_path,
                },
                "meta": {
                    "default_model_id": default_model_id,
                    "model_ids": model_ids,
                    "top_n": top_n,
                    "row_count": len(rows),
                    "raw_candidate_count_default": len(raw_by_model.get(default_model_id, [])),
                    "stable_candidate_count_default": len(stable_by_model.get(default_model_id, [])),
                    "replay_warning": "REPLAY_ONLY_NO_KIWOOM_NO_ORDER",
                },
                "rows": rows,
                "candidates": {
                    "default_model_id": default_model_id,
                    "raw_default": raw_by_model.get(default_model_id, []),
                    "stable_default": stable_by_model.get(default_model_id, []),
                    "raw_by_model": raw_by_model,
                    "stable_by_model": stable_by_model,
                },
            }

            if first_frame is None:
                first_frame = frame
            last_frame = frame

            handle.write(json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n")

            index_minutes.append({
                "minute": minute,
                "minute_index": idx,
                "row_count": len(rows),
                "raw_default_count": len(raw_by_model.get(default_model_id, [])),
                "stable_default_count": len(stable_by_model.get(default_model_id, [])),
            })

    index_payload = {
        "session_id": session,
        "frame_count": len(minutes),
        "first_minute": minutes[0],
        "last_minute": minutes[-1],
        "default_model_id": default_model_id,
        "frames_path": str(frames_path),
        "minutes": index_minutes,
    }

    summary = {
        "session_id": session,
        "frame_count": len(minutes),
        "first_minute": minutes[0],
        "last_minute": minutes[-1],
        "default_model_id": default_model_id,
        "top_n": top_n,
        "inputs": {
            "minute_agg_csv": str(minute_agg_csv),
            "raw_candidate_csv": str(raw_candidate_csv),
            "stable_candidate_csv": str(stable_candidate_csv),
        },
        "tradable_filter": {
            "enabled": True,
            "tradable_master_path": tradable_master_path,
            "tradable_master_code_count": len(tradable_codes) if tradable_codes is not None else None,
            "builtin_filter_enabled": enable_builtin_filter,
            "filter_stats": dict(filter_stats),
            "excluded_total": sum(value for key, value in filter_stats.items() if key.startswith("excluded_")),
        },
        "outputs": {
            "frames_jsonl": str(frames_path),
            "index_json": str(index_path),
            "summary_json": str(summary_path),
            "latest_sample_json": str(latest_sample_path),
        },
        "safety": {
            "replay_only": True,
            "kiwoom_connection": False,
            "order_enabled": False,
            "live_api_reuse": False,
            "recommended_api_prefix": "/api/replay",
        },
    }

    write_json(index_path, index_payload)
    write_json(summary_path, summary)
    if last_frame is not None:
        write_json(latest_sample_path, last_frame)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Build StockBoard replay session frames from replay CSV outputs.")
    parser.add_argument("--out-dir", default="data/runtime/replay_output")
    parser.add_argument("--minute-agg-csv", default=None)
    parser.add_argument("--raw-candidate-csv", default=None)
    parser.add_argument("--stable-candidate-csv", default=None)
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--default-model", default="OPENING_BURST_V01")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--tradable-master", default="docs/tradable_stock_master.csv")
    parser.add_argument("--disable-tradable-master", action="store_true")
    parser.add_argument("--disable-builtin-filter", action="store_true")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    minute_agg_csv = Path(args.minute_agg_csv) if args.minute_agg_csv else latest_file(out_dir, "*_minute_agg.csv")
    raw_candidate_csv = Path(args.raw_candidate_csv) if args.raw_candidate_csv else latest_file(
        out_dir,
        "*_candidate_replay_*_top5.csv",
        exclude=("_stable_",),
    )
    stable_candidate_csv = Path(args.stable_candidate_csv) if args.stable_candidate_csv else latest_file(
        out_dir,
        "*_candidate_replay_*_stable_top5.csv",
    )

    tradable_codes: set[str] | None = None
    tradable_master_path: str | None = None
    if not args.disable_tradable_master:
        tradable_codes, tradable_master_path = load_tradable_codes(Path(args.tradable_master))

    summary = build_frames(
        minute_agg_csv=minute_agg_csv,
        raw_candidate_csv=raw_candidate_csv,
        stable_candidate_csv=stable_candidate_csv,
        out_dir=out_dir,
        top_n=args.top_n,
        default_model_id=args.default_model,
        session_id=args.session_id,
        tradable_codes=tradable_codes,
        tradable_master_path=tradable_master_path,
        enable_builtin_filter=not args.disable_builtin_filter,
    )

    print("REPLAY_STOCKBOARD_FRAMES_OK")
    print("session_id:", summary["session_id"])
    print("frame_count:", summary["frame_count"])
    print("first_minute:", summary["first_minute"])
    print("last_minute:", summary["last_minute"])
    print("default_model_id:", summary["default_model_id"])
    print("top_n:", summary["top_n"])
    print("tradable_filter:", summary["tradable_filter"])
    print("frames_jsonl:", summary["outputs"]["frames_jsonl"])
    print("index_json:", summary["outputs"]["index_json"])
    print("summary_json:", summary["outputs"]["summary_json"])
    print("latest_sample_json:", summary["outputs"]["latest_sample_json"])
    print("safety:", summary["safety"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())