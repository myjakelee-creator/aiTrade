from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stockboard_engine import enrich_candidate_fields, load_candidate_score_model  # noqa: E402


DEFAULT_MODELS = [
    "OPENING_MONEY_FLOW_V01",
    "OPENING_BURST_V01",
    "PROGRAM_FLOW_V01",
]


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


def latest_minute_agg_csv(out_dir: Path) -> Path:
    files = sorted(out_dir.glob("*_minute_agg.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError(f"No *_minute_agg.csv found in {out_dir}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def row_sort_trade_value(row: dict[str, Any]) -> tuple[float, float, float]:
    return (
        parse_number(row.get("minute_trade_value_eok_est")) or 0,
        parse_number(row.get("trade_tick_count")) or 0,
        parse_number(row.get("acc_volume")) or 0,
    )


def build_replay_rows_for_minute(minute_rows: list[dict[str, Any]], prev_rank_by_code: dict[str, int]) -> list[dict[str, Any]]:
    ranked = sorted(minute_rows, key=row_sort_trade_value, reverse=True)
    current_rank_by_code: dict[str, int] = {}

    replay_rows: list[dict[str, Any]] = []
    for rank, source in enumerate(ranked, start=1):
        stock_code = clean_code(source.get("stock_code"))
        if not stock_code:
            continue

        current_rank_by_code[stock_code] = rank
        prev_rank = prev_rank_by_code.get(stock_code)

        minute_trade_value = parse_number(source.get("minute_trade_value_eok_est"))
        close = parse_number(source.get("close"))

        ohlc = {
            "open": parse_number(source.get("open")),
            "high": parse_number(source.get("high")),
            "low": parse_number(source.get("low")),
            "close": close,
        }

        replay_row = {
            "stock_code": stock_code,
            "stock_name": source.get("stock_name"),
            "rank": rank,
            "displayed_rank": rank,
            "prev_rank": prev_rank,
            "rank_diff": (prev_rank - rank) if prev_rank is not None else None,
            "price": close,
            "display_price": close,
            "realtime_price": close,
            "change_rate": parse_number(source.get("last_change_rate")),
            "display_change_rate": parse_number(source.get("last_change_rate")),
            "realtime_change_rate": parse_number(source.get("last_change_rate")),
            "trade_value_eok": minute_trade_value,
            "one_min_trade_value_eok": minute_trade_value,
            "one_min_trade_value_delta_eok": minute_trade_value,
            "ohlc": ohlc,
            "display_ohlc": ohlc,
            "realtime_ohlc": ohlc,
            "bid_ask_ratio": parse_number(source.get("bid_ask_ratio")),
            "strength_1m": parse_number(source.get("execution_strength")),
            "strength_day": parse_number(source.get("execution_strength")),
            "realtime_strength": parse_number(source.get("execution_strength")),
            "execution_strength": parse_number(source.get("execution_strength")),
            "program_net": parse_number(source.get("program_net")),
            "minute_volume_est": parse_number(source.get("minute_volume_est")),
            "acc_volume": parse_number(source.get("acc_volume")),
            "trade_metric_7_open": parse_number(source.get("trade_metric_7_open")),
            "trade_metric_7_high": parse_number(source.get("trade_metric_7_high")),
            "trade_metric_7_low": parse_number(source.get("trade_metric_7_low")),
            "trade_metric_7_close": parse_number(source.get("trade_metric_7_close")),
            "trade_metric_7_delta_in_min": parse_number(source.get("trade_metric_7_delta_in_min")),
            "trade_metric_7_delta_from_prev_min": parse_number(source.get("trade_metric_7_delta_from_prev_min")),
            "trade_metric_7_sum_debug": parse_number(source.get("trade_metric_7_sum_debug")),
            # Deliberately not mapped:
            # one_min_net_buy_value_delta_eok is withheld until TRADE[7] meaning is confirmed.
            "replay_minute": source.get("minute_kst"),
            "replay_source": "live_raw_minute_agg",
        }
        replay_rows.append(replay_row)

    prev_rank_by_code.clear()
    prev_rank_by_code.update(current_rank_by_code)
    return replay_rows


def safe_model_id(model: dict[str, Any], requested: str) -> str:
    return str(model.get("id") or model.get("version") or requested)


def replay_models(
    agg_csv: Path,
    out_dir: Path,
    model_ids: list[str],
    candidate_limit: int,
    include_all_top: int,
) -> dict[str, Any]:
    rows = read_csv(agg_csv)
    by_minute: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        minute = str(row.get("minute_kst") or "")
        if minute:
            by_minute[minute].append(row)

    minutes = sorted(by_minute)

    model_objects = []
    for model_id in model_ids:
        model = load_candidate_score_model(model_id)
        model_objects.append((model_id, model))

    candidate_rows_out: list[dict[str, Any]] = []
    all_top_rows_out: list[dict[str, Any]] = []
    transition_rows_out: list[dict[str, Any]] = []

    prev_rank_by_code: dict[str, int] = {}
    prev_candidates_by_model: dict[str, set[str]] = {model_id: set() for model_id in model_ids}

    for minute in minutes:
        replay_rows = build_replay_rows_for_minute(by_minute[minute], prev_rank_by_code)

        for requested_model_id, model in model_objects:
            resolved_model_id = safe_model_id(model, requested_model_id)
            enriched = enrich_candidate_fields(deepcopy(replay_rows), model=model)

            candidates = [
                row for row in enriched
                if isinstance(row.get("candidate_rank"), int)
            ]
            candidates.sort(key=lambda row: row.get("candidate_rank") or 999)

            top_ranked = sorted(
                enriched,
                key=lambda row: (
                    row.get("funnel_rank") if isinstance(row.get("funnel_rank"), int) else 999999,
                    row.get("rank") if isinstance(row.get("rank"), int) else 999999,
                ),
            )[:include_all_top]

            current_candidates = {
                str(row.get("stock_code"))
                for row in candidates[:candidate_limit]
                if row.get("stock_code")
            }
            previous_candidates = prev_candidates_by_model.get(requested_model_id, set())

            entered = sorted(current_candidates - previous_candidates)
            exited = sorted(previous_candidates - current_candidates)

            transition_rows_out.append({
                "replay_minute": minute,
                "requested_model_id": requested_model_id,
                "resolved_model_id": resolved_model_id,
                "entered": ",".join(entered),
                "exited": ",".join(exited),
                "candidate_codes": ",".join(sorted(current_candidates)),
            })
            prev_candidates_by_model[requested_model_id] = current_candidates

            for row in candidates[:candidate_limit]:
                candidate_rows_out.append(output_row(minute, requested_model_id, resolved_model_id, row))

            for row in top_ranked:
                next_row = output_row(minute, requested_model_id, resolved_model_id, row)
                next_row["output_group"] = "top_ranked"
                all_top_rows_out.append(next_row)

    prefix = agg_csv.stem.replace("_minute_agg", "")
    models_label = "_".join(model_ids)
    safe_models_label = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in models_label)

    candidate_csv = out_dir / f"{prefix}_candidate_replay_{safe_models_label}_top{candidate_limit}.csv"
    top_csv = out_dir / f"{prefix}_candidate_replay_{safe_models_label}_ranked_top{include_all_top}.csv"
    transition_csv = out_dir / f"{prefix}_candidate_replay_{safe_models_label}_transitions.csv"
    summary_json = out_dir / f"{prefix}_candidate_replay_{safe_models_label}_summary.json"

    fieldnames = output_fieldnames()
    write_csv(candidate_csv, candidate_rows_out, fieldnames)
    write_csv(top_csv, all_top_rows_out, ["output_group"] + fieldnames)
    write_csv(transition_csv, transition_rows_out, [
        "replay_minute",
        "requested_model_id",
        "resolved_model_id",
        "entered",
        "exited",
        "candidate_codes",
    ])

    summary = {
        "agg_csv": str(agg_csv),
        "minute_count": len(minutes),
        "models": [
            {
                "requested_model_id": requested,
                "resolved_model_id": safe_model_id(model, requested),
                "name": model.get("name"),
            }
            for requested, model in model_objects
        ],
        "candidate_limit": candidate_limit,
        "include_all_top": include_all_top,
        "candidate_rows": len(candidate_rows_out),
        "ranked_top_rows": len(all_top_rows_out),
        "transition_rows": len(transition_rows_out),
        "outputs": {
            "candidate_csv": str(candidate_csv),
            "ranked_top_csv": str(top_csv),
            "transition_csv": str(transition_csv),
            "summary_json": str(summary_json),
        },
        "notes": {
            "trade_metric_7": "Included for observation only. Not mapped to one_min_net_buy_value_delta_eok.",
            "trade_value_eok": "Uses minute_trade_value_eok_est from replay parser.",
            "rank": "Minute trade value rank within replay minute.",
        },
    }
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def output_fieldnames() -> list[str]:
    return [
        "replay_minute",
        "requested_model_id",
        "resolved_model_id",
        "candidate_rank",
        "funnel_rank",
        "pool_stage",
        "pool_rank",
        "rank",
        "prev_rank",
        "rank_diff",
        "stock_code",
        "stock_name",
        "candidate_score",
        "candidate_grade_text",
        "candidate_status",
        "entry_score",
        "confirmation_score",
        "focus_score",
        "score_top50",
        "score_top20",
        "score_top5",
        "grade_score",
        "price",
        "change_rate",
        "trade_value_eok",
        "minute_volume_est",
        "execution_strength",
        "bid_ask_ratio",
        "program_net",
        "trade_metric_7_open",
        "trade_metric_7_high",
        "trade_metric_7_low",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "trade_metric_7_delta_from_prev_min",
        "candidate_reason",
    ]


def output_row(minute: str, requested_model_id: str, resolved_model_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "replay_minute": minute,
        "requested_model_id": requested_model_id,
        "resolved_model_id": resolved_model_id,
        "candidate_rank": row.get("candidate_rank"),
        "funnel_rank": row.get("funnel_rank"),
        "pool_stage": row.get("pool_stage"),
        "pool_rank": row.get("pool_rank"),
        "rank": row.get("rank"),
        "prev_rank": row.get("prev_rank"),
        "rank_diff": row.get("rank_diff"),
        "stock_code": row.get("stock_code"),
        "stock_name": row.get("stock_name"),
        "candidate_score": row.get("candidate_score"),
        "candidate_grade_text": row.get("candidate_grade_text"),
        "candidate_status": row.get("candidate_status"),
        "entry_score": row.get("entry_score"),
        "confirmation_score": row.get("confirmation_score"),
        "focus_score": row.get("focus_score"),
        "score_top50": row.get("score_top50"),
        "score_top20": row.get("score_top20"),
        "score_top5": row.get("score_top5"),
        "grade_score": row.get("grade_score"),
        "price": row.get("price"),
        "change_rate": row.get("change_rate"),
        "trade_value_eok": row.get("trade_value_eok"),
        "minute_volume_est": row.get("minute_volume_est"),
        "execution_strength": row.get("execution_strength"),
        "bid_ask_ratio": row.get("bid_ask_ratio"),
        "program_net": row.get("program_net"),
        "trade_metric_7_open": row.get("trade_metric_7_open"),
        "trade_metric_7_high": row.get("trade_metric_7_high"),
        "trade_metric_7_low": row.get("trade_metric_7_low"),
        "trade_metric_7_close": row.get("trade_metric_7_close"),
        "trade_metric_7_delta_in_min": row.get("trade_metric_7_delta_in_min"),
        "trade_metric_7_delta_from_prev_min": row.get("trade_metric_7_delta_from_prev_min"),
        "candidate_reason": row.get("candidate_reason"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay StockBoard candidate models from minute aggregate CSV.")
    parser.add_argument("--agg-csv", default=None, help="Path to *_minute_agg.csv. Defaults to latest in out-dir.")
    parser.add_argument("--out-dir", default="data/runtime/replay_output")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--candidate-limit", type=int, default=5)
    parser.add_argument("--include-all-top", type=int, default=30)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    agg_csv = Path(args.agg_csv) if args.agg_csv else latest_minute_agg_csv(out_dir)
    summary = replay_models(
        agg_csv=agg_csv,
        out_dir=out_dir,
        model_ids=args.models,
        candidate_limit=args.candidate_limit,
        include_all_top=args.include_all_top,
    )

    print("REPLAY_CANDIDATE_MODELS_OK")
    print("agg_csv:", summary["agg_csv"])
    print("minute_count:", summary["minute_count"])
    print("models:", summary["models"])
    print("candidate_rows:", summary["candidate_rows"])
    print("ranked_top_rows:", summary["ranked_top_rows"])
    print("transition_rows:", summary["transition_rows"])
    print("candidate_csv:", summary["outputs"]["candidate_csv"])
    print("ranked_top_csv:", summary["outputs"]["ranked_top_csv"])
    print("transition_csv:", summary["outputs"]["transition_csv"])
    print("summary_json:", summary["outputs"]["summary_json"])
    print("notes:", summary["notes"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())