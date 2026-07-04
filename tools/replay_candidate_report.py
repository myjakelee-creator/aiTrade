from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


GRADE_ORDER = {"A": 1, "B": 2, "C": 3, "D": 4, "F": 5}


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


def grade_letter(grade_text: Any) -> str:
    text = str(grade_text or "").strip()
    return text[:1] if text else ""


def is_grade_at_least_c(grade_text: Any) -> bool:
    letter = grade_letter(grade_text)
    return letter in {"A", "B", "C"}


def latest_file(out_dir: Path, pattern: str) -> Path:
    files = sorted(out_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise RuntimeError(f"No file found: {out_dir / pattern}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def avg(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def top_counter(counter: Counter, n: int = 20) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(n)]


def model_grade_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped = Counter(
        (row.get("requested_model_id"), row.get("candidate_grade_text"))
        for row in rows
    )
    result = []
    for (model_id, grade_text), count in sorted(grouped.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        result.append({
            "model_id": model_id,
            "candidate_grade_text": grade_text,
            "grade_letter": grade_letter(grade_text),
            "count": count,
        })
    return result


def model_score_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[str(row.get("requested_model_id"))].append(row)

    result = []
    for model_id, model_rows in sorted(by_model.items()):
        scores = [parse_number(row.get("candidate_score")) for row in model_rows]
        scores = [value for value in scores if value is not None]
        c_or_better = [row for row in model_rows if is_grade_at_least_c(row.get("candidate_grade_text"))]
        d_or_better = [row for row in model_rows if grade_letter(row.get("candidate_grade_text")) in {"A", "B", "C", "D"}]
        result.append({
            "model_id": model_id,
            "row_count": len(model_rows),
            "score_min": min(scores) if scores else None,
            "score_max": max(scores) if scores else None,
            "score_avg": avg(scores),
            "c_or_better_count": len(c_or_better),
            "d_or_better_count": len(d_or_better),
            "c_or_better_ratio": round(len(c_or_better) / len(model_rows), 4) if model_rows else None,
            "d_or_better_ratio": round(len(d_or_better) / len(model_rows), 4) if model_rows else None,
        })
    return result


def repeated_candidates(rows: list[dict[str, Any]], n: int = 30) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("requested_model_id")), str(row.get("stock_code")), str(row.get("stock_name")))].append(row)

    result = []
    for (model_id, stock_code, stock_name), stock_rows in grouped.items():
        minutes = sorted({str(row.get("replay_minute")) for row in stock_rows})
        scores = [parse_number(row.get("candidate_score")) for row in stock_rows]
        scores = [value for value in scores if value is not None]
        best = max(stock_rows, key=lambda row: parse_number(row.get("candidate_score")) or -1)
        result.append({
            "model_id": model_id,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "appear_count": len(stock_rows),
            "first_minute": minutes[0] if minutes else None,
            "last_minute": minutes[-1] if minutes else None,
            "best_score": parse_number(best.get("candidate_score")),
            "best_grade": best.get("candidate_grade_text"),
            "avg_score": avg(scores),
            "minutes": ",".join(minutes),
        })

    result.sort(key=lambda row: (row["appear_count"], row["best_score"] or 0), reverse=True)
    return result[:n]


def c_or_better_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if not is_grade_at_least_c(row.get("candidate_grade_text")):
            continue
        result.append({
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get("candidate_rank"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "candidate_score": row.get("candidate_score"),
            "candidate_grade_text": row.get("candidate_grade_text"),
            "trade_value_eok": row.get("trade_value_eok"),
            "execution_strength": row.get("execution_strength"),
            "trade_metric_7_close": row.get("trade_metric_7_close"),
            "trade_metric_7_delta_in_min": row.get("trade_metric_7_delta_in_min"),
            "trade_metric_7_delta_from_prev_min": row.get("trade_metric_7_delta_from_prev_min"),
            "bid_ask_ratio": row.get("bid_ask_ratio"),
            "program_net": row.get("program_net"),
        })
    result.sort(key=lambda row: (row["replay_minute"], row["model_id"], int(row["candidate_rank"] or 999)))
    return result


def transition_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        entered = [item for item in str(row.get("entered") or "").split(",") if item]
        exited = [item for item in str(row.get("exited") or "").split(",") if item]
        result.append({
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "entered_count": len(entered),
            "exited_count": len(exited),
            "entered": row.get("entered"),
            "exited": row.get("exited"),
            "candidate_codes": row.get("candidate_codes"),
        })
    return result


def churn_by_model(transition_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in transition_rows:
        by_model[str(row.get("model_id"))].append(row)

    result = []
    for model_id, model_rows in sorted(by_model.items()):
        entered_counts = [int(row["entered_count"]) for row in model_rows]
        exited_counts = [int(row["exited_count"]) for row in model_rows]
        result.append({
            "model_id": model_id,
            "minutes": len(model_rows),
            "entered_total": sum(entered_counts),
            "exited_total": sum(exited_counts),
            "entered_avg": avg([float(value) for value in entered_counts]),
            "exited_avg": avg([float(value) for value in exited_counts]),
            "full_turnover_minutes": sum(1 for row in model_rows if int(row["entered_count"]) >= 5),
        })
    return result


def trade_metric_7_observation(rows: list[dict[str, Any]], n: int = 30) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        delta = parse_number(row.get("trade_metric_7_delta_in_min"))
        prev_delta = parse_number(row.get("trade_metric_7_delta_from_prev_min"))
        close = parse_number(row.get("trade_metric_7_close"))
        score = parse_number(row.get("candidate_score"))
        if delta is None and prev_delta is None and close is None:
            continue
        candidates.append({
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get("candidate_rank"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "candidate_score": score,
            "candidate_grade_text": row.get("candidate_grade_text"),
            "trade_metric_7_close": close,
            "trade_metric_7_delta_in_min": delta,
            "trade_metric_7_delta_from_prev_min": prev_delta,
            "execution_strength": parse_number(row.get("execution_strength")),
            "trade_value_eok": parse_number(row.get("trade_value_eok")),
        })

    candidates.sort(
        key=lambda row: max(
            abs(row.get("trade_metric_7_delta_in_min") or 0),
            abs(row.get("trade_metric_7_delta_from_prev_min") or 0),
            abs(row.get("trade_metric_7_close") or 0),
        ),
        reverse=True,
    )
    return candidates[:n]


def timeline_by_repeated_top(rows: list[dict[str, Any]], repeated: list[dict[str, Any]], limit_stocks: int = 10) -> list[dict[str, Any]]:
    target_keys = {
        (row["model_id"], row["stock_code"])
        for row in repeated[:limit_stocks]
    }
    result = []
    for row in rows:
        key = (str(row.get("requested_model_id")), str(row.get("stock_code")))
        if key not in target_keys:
            continue
        result.append({
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get("candidate_rank"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "candidate_score": row.get("candidate_score"),
            "candidate_grade_text": row.get("candidate_grade_text"),
            "trade_value_eok": row.get("trade_value_eok"),
            "execution_strength": row.get("execution_strength"),
            "trade_metric_7_close": row.get("trade_metric_7_close"),
            "trade_metric_7_delta_in_min": row.get("trade_metric_7_delta_in_min"),
            "bid_ask_ratio": row.get("bid_ask_ratio"),
        })
    result.sort(key=lambda row: (row["model_id"], row["stock_code"], row["replay_minute"]))
    return result


def write_report_txt(path: Path, report: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("StockBoard Replay Candidate Report")
    lines.append("=" * 80)
    lines.append(f"candidate_csv: {report['inputs']['candidate_csv']}")
    lines.append(f"transition_csv: {report['inputs']['transition_csv']}")
    lines.append(f"row_count: {report['inputs']['candidate_row_count']}")
    lines.append("")

    lines.append("MODEL SCORE SUMMARY")
    for row in report["model_score_summary"]:
        lines.append(
            f"- {row['model_id']}: rows={row['row_count']}, "
            f"score_avg={row['score_avg']}, score_max={row['score_max']}, "
            f"C+={row['c_or_better_count']} ({row['c_or_better_ratio']}), "
            f"D+={row['d_or_better_count']} ({row['d_or_better_ratio']})"
        )
    lines.append("")

    lines.append("GRADE DISTRIBUTION")
    for row in report["model_grade_summary"]:
        lines.append(f"- {row['model_id']} {row['candidate_grade_text']}: {row['count']}")
    lines.append("")

    lines.append("REPEATED CANDIDATES TOP")
    for row in report["repeated_candidates"]:
        lines.append(
            f"- {row['model_id']} {row['stock_code']} {row['stock_name']}: "
            f"appear={row['appear_count']}, best={row['best_grade']}, avg={row['avg_score']}, "
            f"first={row['first_minute']}, last={row['last_minute']}"
        )
    lines.append("")

    lines.append("C OR BETTER CANDIDATES")
    for row in report["c_or_better"]:
        lines.append(
            f"- {row['replay_minute']} {row['model_id']} rank={row['candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} {row['candidate_grade_text']} "
            f"score={row['candidate_score']} tv={row['trade_value_eok']} "
            f"strength={row['execution_strength']} m7={row['trade_metric_7_close']} "
            f"m7_delta={row['trade_metric_7_delta_in_min']}"
        )
    lines.append("")

    lines.append("CHURN BY MODEL")
    for row in report["churn_by_model"]:
        lines.append(
            f"- {row['model_id']}: minutes={row['minutes']}, "
            f"entered_total={row['entered_total']}, exited_total={row['exited_total']}, "
            f"entered_avg={row['entered_avg']}, exited_avg={row['exited_avg']}, "
            f"full_turnover_minutes={row['full_turnover_minutes']}"
        )
    lines.append("")

    lines.append("TRADE_METRIC_7 OBSERVATION TOP")
    for row in report["trade_metric_7_observation"]:
        lines.append(
            f"- {row['replay_minute']} {row['model_id']} rank={row['candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} score={row['candidate_score']} "
            f"m7_close={row['trade_metric_7_close']} "
            f"m7_delta={row['trade_metric_7_delta_in_min']} "
            f"m7_prev_delta={row['trade_metric_7_delta_from_prev_min']} "
            f"strength={row['execution_strength']} tv={row['trade_value_eok']}"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_report(candidate_csv: Path, transition_csv: Path, out_dir: Path) -> dict[str, Any]:
    candidate_rows = read_csv(candidate_csv)
    transition_rows_raw = read_csv(transition_csv)

    # Report is focused on 09:00+ by default because 08:50~08:59 is pre-open/fallback-heavy.
    focus_rows = [row for row in candidate_rows if str(row.get("replay_minute") or "") >= "2026-06-16T09:00"]
    transitions = transition_summary([
        row for row in transition_rows_raw
        if str(row.get("replay_minute") or "") >= "2026-06-16T09:00"
    ])

    repeated = repeated_candidates(focus_rows, n=30)

    report = {
        "inputs": {
            "candidate_csv": str(candidate_csv),
            "transition_csv": str(transition_csv),
            "candidate_row_count": len(candidate_rows),
            "focus_row_count_0900_plus": len(focus_rows),
            "transition_row_count_0900_plus": len(transitions),
        },
        "model_score_summary": model_score_summary(focus_rows),
        "model_grade_summary": model_grade_summary(focus_rows),
        "repeated_candidates": repeated,
        "c_or_better": c_or_better_rows(focus_rows),
        "transition_summary": transitions,
        "churn_by_model": churn_by_model(transitions),
        "trade_metric_7_observation": trade_metric_7_observation(focus_rows, n=30),
        "timeline_for_repeated_candidates": timeline_by_repeated_top(focus_rows, repeated, limit_stocks=10),
        "notes": {
            "focus_window": "Rows with replay_minute >= 2026-06-16T09:00.",
            "trade_metric_7": "Unresolved diagnostic metric. Observe movement only; do not treat as scoring input yet.",
            "grades": "Uses restored legacy bands A91/B81/C71/D61/F.",
        },
    }

    json_path = out_dir / "replay_candidate_report_latest.json"
    txt_path = out_dir / "replay_candidate_report_latest.txt"
    repeated_csv = out_dir / "replay_candidate_report_repeated_latest.csv"
    cplus_csv = out_dir / "replay_candidate_report_c_or_better_latest.csv"
    metric7_csv = out_dir / "replay_candidate_report_trade_metric_7_latest.csv"
    timeline_csv = out_dir / "replay_candidate_report_timeline_latest.csv"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report_txt(txt_path, report)

    write_csv(repeated_csv, report["repeated_candidates"], [
        "model_id",
        "stock_code",
        "stock_name",
        "appear_count",
        "first_minute",
        "last_minute",
        "best_score",
        "best_grade",
        "avg_score",
        "minutes",
    ])
    write_csv(cplus_csv, report["c_or_better"], [
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "candidate_score",
        "candidate_grade_text",
        "trade_value_eok",
        "execution_strength",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "trade_metric_7_delta_from_prev_min",
        "bid_ask_ratio",
        "program_net",
    ])
    write_csv(metric7_csv, report["trade_metric_7_observation"], [
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "candidate_score",
        "candidate_grade_text",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "trade_metric_7_delta_from_prev_min",
        "execution_strength",
        "trade_value_eok",
    ])
    write_csv(timeline_csv, report["timeline_for_repeated_candidates"], [
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "candidate_score",
        "candidate_grade_text",
        "trade_value_eok",
        "execution_strength",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "bid_ask_ratio",
    ])

    report["outputs"] = {
        "json": str(json_path),
        "txt": str(txt_path),
        "repeated_csv": str(repeated_csv),
        "c_or_better_csv": str(cplus_csv),
        "trade_metric_7_csv": str(metric7_csv),
        "timeline_csv": str(timeline_csv),
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a human-readable report from StockBoard candidate replay CSVs.")
    parser.add_argument("--out-dir", default="data/runtime/replay_output")
    parser.add_argument("--candidate-csv", default=None)
    parser.add_argument("--transition-csv", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate_csv = Path(args.candidate_csv) if args.candidate_csv else latest_file(out_dir, "*_candidate_replay_*_top5.csv")
    transition_csv = Path(args.transition_csv) if args.transition_csv else latest_file(out_dir, "*_candidate_replay_*_transitions.csv")

    report = build_report(candidate_csv, transition_csv, out_dir)

    print("REPLAY_CANDIDATE_REPORT_OK")
    print("candidate_csv:", report["inputs"]["candidate_csv"])
    print("transition_csv:", report["inputs"]["transition_csv"])
    print("focus_row_count_0900_plus:", report["inputs"]["focus_row_count_0900_plus"])
    print("model_score_summary:", report["model_score_summary"])
    print("c_or_better_count:", len(report["c_or_better"]))
    print("repeated_top5:", report["repeated_candidates"][:5])
    print("churn_by_model:", report["churn_by_model"])
    print("outputs:", report["outputs"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())