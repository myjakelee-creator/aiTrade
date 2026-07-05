from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


FOCUS_START_MINUTE = "2026-06-16T09:00"


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
    return grade_letter(grade_text) in {"A", "B", "C"}


def is_grade_at_least_d(grade_text: Any) -> bool:
    return grade_letter(grade_text) in {"A", "B", "C", "D"}


def avg(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def pct_reduction(before: float | None, after: float | None) -> float | None:
    if before in (None, 0) or after is None:
        return None
    return round((before - after) / before, 4)


def latest_file(out_dir: Path, pattern: str, exclude: tuple[str, ...] = ()) -> Path:
    files = sorted(out_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    files = [path for path in files if not any(token in path.name for token in exclude)]
    if not files:
        raise RuntimeError(f"No file found: pattern={pattern}, exclude={exclude}, out_dir={out_dir}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def focus_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("replay_minute") or "") >= FOCUS_START_MINUTE]


def split_codes(value: Any) -> list[str]:
    return [item for item in str(value or "").split(",") if item]


def model_score_summary(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    score_field: str,
    grade_field: str,
) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_model[str(row.get("requested_model_id"))].append(row)

    result = []
    for model_id, model_rows in sorted(by_model.items()):
        scores = [parse_number(row.get(score_field)) for row in model_rows]
        scores = [value for value in scores if value is not None]
        c_or_better = [row for row in model_rows if is_grade_at_least_c(row.get(grade_field))]
        d_or_better = [row for row in model_rows if is_grade_at_least_d(row.get(grade_field))]
        result.append({
            "mode": mode,
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


def model_grade_summary(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    grade_field: str,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        grouped[(str(row.get("requested_model_id")), str(row.get(grade_field)))] += 1

    result = []
    for (model_id, grade_text), count in sorted(grouped.items(), key=lambda item: (item[0][0], item[0][1])):
        result.append({
            "mode": mode,
            "model_id": model_id,
            "grade_text": grade_text,
            "grade_letter": grade_letter(grade_text),
            "count": count,
        })
    return result


def repeated_candidates(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    score_field: str,
    grade_field: str,
    n: int = 30,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("requested_model_id")), str(row.get("stock_code")), str(row.get("stock_name")))].append(row)

    result = []
    for (model_id, stock_code, stock_name), stock_rows in grouped.items():
        minutes = sorted({str(row.get("replay_minute")) for row in stock_rows})
        scores = [parse_number(row.get(score_field)) for row in stock_rows]
        scores = [value for value in scores if value is not None]
        best = max(stock_rows, key=lambda row: parse_number(row.get(score_field)) or -1)
        result.append({
            "mode": mode,
            "model_id": model_id,
            "stock_code": stock_code,
            "stock_name": stock_name,
            "appear_count": len(stock_rows),
            "first_minute": minutes[0] if minutes else None,
            "last_minute": minutes[-1] if minutes else None,
            "best_score": parse_number(best.get(score_field)),
            "best_grade": best.get(grade_field),
            "avg_score": avg(scores),
            "minutes": ",".join(minutes),
        })

    result.sort(key=lambda row: (row["appear_count"], row["best_score"] or 0), reverse=True)
    return result[:n]


def c_or_better_rows(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    rank_field: str,
    score_field: str,
    grade_field: str,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if not is_grade_at_least_c(row.get(grade_field)):
            continue
        result.append({
            "mode": mode,
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get(rank_field),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "score": row.get(score_field),
            "grade_text": row.get(grade_field),
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


def d_or_better_rows(
    rows: list[dict[str, Any]],
    *,
    mode: str,
    rank_field: str,
    score_field: str,
    grade_field: str,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        if not is_grade_at_least_d(row.get(grade_field)):
            continue
        result.append({
            "mode": mode,
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get(rank_field),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "score": row.get(score_field),
            "grade_text": row.get(grade_field),
            "trade_value_eok": row.get("trade_value_eok"),
            "execution_strength": row.get("execution_strength"),
            "trade_metric_7_close": row.get("trade_metric_7_close"),
            "trade_metric_7_delta_in_min": row.get("trade_metric_7_delta_in_min"),
            "stable_prev": row.get("stability_prev_stable"),
            "stability_recent_raw_count": row.get("stability_recent_raw_count"),
            "stability_recent_stable_count": row.get("stability_recent_stable_count"),
            "stability_penalty": row.get("stability_penalty"),
        })
    result.sort(key=lambda row: (row["replay_minute"], row["model_id"], int(row["candidate_rank"] or 999)))
    return result


def transition_summary(rows: list[dict[str, Any]], *, mode: str) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        entered = split_codes(row.get("entered"))
        exited = split_codes(row.get("exited"))
        result.append({
            "mode": mode,
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "entered_count": len(entered),
            "exited_count": len(exited),
            "entered": row.get("entered"),
            "exited": row.get("exited"),
            "candidate_codes": row.get("candidate_codes"),
        })
    return result


def churn_by_model(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
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


def raw_vs_stable_churn(raw_churn: list[dict[str, Any]], stable_churn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_by_model = {row["model_id"]: row for row in raw_churn}
    stable_by_model = {row["model_id"]: row for row in stable_churn}
    result = []

    for model_id in sorted(set(raw_by_model) | set(stable_by_model)):
        raw = raw_by_model.get(model_id, {})
        stable = stable_by_model.get(model_id, {})
        raw_entered_avg = parse_number(raw.get("entered_avg"))
        stable_entered_avg = parse_number(stable.get("entered_avg"))
        raw_full = parse_number(raw.get("full_turnover_minutes"))
        stable_full = parse_number(stable.get("full_turnover_minutes"))

        result.append({
            "model_id": model_id,
            "raw_entered_avg": raw_entered_avg,
            "stable_entered_avg": stable_entered_avg,
            "entered_avg_reduction_ratio": pct_reduction(raw_entered_avg, stable_entered_avg),
            "raw_exited_avg": parse_number(raw.get("exited_avg")),
            "stable_exited_avg": parse_number(stable.get("exited_avg")),
            "raw_full_turnover_minutes": raw_full,
            "stable_full_turnover_minutes": stable_full,
            "full_turnover_reduction_ratio": pct_reduction(raw_full, stable_full),
            "raw_entered_total": raw.get("entered_total"),
            "stable_entered_total": stable.get("entered_total"),
            "raw_exited_total": raw.get("exited_total"),
            "stable_exited_total": stable.get("exited_total"),
        })
    return result


def top_stable_scores(rows: list[dict[str, Any]], n: int = 30) -> list[dict[str, Any]]:
    ranked = sorted(rows, key=lambda row: parse_number(row.get("stable_score")) or -1, reverse=True)
    result = []
    for row in ranked[:n]:
        result.append({
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "stable_candidate_rank": row.get("stable_candidate_rank"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "stable_score": row.get("stable_score"),
            "stable_grade_text": row.get("stable_grade_text"),
            "candidate_score": row.get("candidate_score"),
            "candidate_grade_text": row.get("candidate_grade_text"),
            "trade_value_eok": row.get("trade_value_eok"),
            "execution_strength": row.get("execution_strength"),
            "stability_recent_raw_count": row.get("stability_recent_raw_count"),
            "stability_recent_stable_count": row.get("stability_recent_stable_count"),
            "stability_prev_stable": row.get("stability_prev_stable"),
            "stability_penalty": row.get("stability_penalty"),
        })
    return result


def timeline_by_repeated_top(
    rows: list[dict[str, Any]],
    repeated: list[dict[str, Any]],
    *,
    mode: str,
    rank_field: str,
    score_field: str,
    grade_field: str,
    limit_stocks: int = 10,
) -> list[dict[str, Any]]:
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
            "mode": mode,
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get(rank_field),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "score": row.get(score_field),
            "grade_text": row.get(grade_field),
            "trade_value_eok": row.get("trade_value_eok"),
            "execution_strength": row.get("execution_strength"),
            "trade_metric_7_close": row.get("trade_metric_7_close"),
            "trade_metric_7_delta_in_min": row.get("trade_metric_7_delta_in_min"),
            "bid_ask_ratio": row.get("bid_ask_ratio"),
        })
    result.sort(key=lambda row: (row["mode"], row["model_id"], row["stock_code"], row["replay_minute"]))
    return result


def trade_metric_7_observation(rows: list[dict[str, Any]], *, mode: str, score_field: str, grade_field: str, n: int = 30) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        delta = parse_number(row.get("trade_metric_7_delta_in_min"))
        prev_delta = parse_number(row.get("trade_metric_7_delta_from_prev_min"))
        close = parse_number(row.get("trade_metric_7_close"))
        score = parse_number(row.get(score_field))
        if delta is None and prev_delta is None and close is None:
            continue
        candidates.append({
            "mode": mode,
            "replay_minute": row.get("replay_minute"),
            "model_id": row.get("requested_model_id"),
            "candidate_rank": row.get("candidate_rank") or row.get("stable_candidate_rank"),
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "score": score,
            "grade_text": row.get(grade_field),
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


def write_report_txt(path: Path, report: dict[str, Any]) -> None:
    lines: list[str] = []
    lines.append("StockBoard Replay Candidate Report - Raw vs Stable")
    lines.append("=" * 80)
    lines.append(f"candidate_csv: {report['inputs']['candidate_csv']}")
    lines.append(f"stable_candidate_csv: {report['inputs']['stable_candidate_csv']}")
    lines.append(f"transition_csv: {report['inputs']['transition_csv']}")
    lines.append(f"stable_transition_csv: {report['inputs']['stable_transition_csv']}")
    lines.append(f"focus_start_minute: {FOCUS_START_MINUTE}")
    lines.append(f"raw_focus_rows: {report['inputs']['raw_focus_row_count']}")
    lines.append(f"stable_focus_rows: {report['inputs']['stable_focus_row_count']}")
    lines.append("")

    lines.append("RAW VS STABLE CHURN")
    for row in report["raw_vs_stable_churn"]:
        lines.append(
            f"- {row['model_id']}: raw_entered_avg={row['raw_entered_avg']}, "
            f"stable_entered_avg={row['stable_entered_avg']}, "
            f"entered_reduction={row['entered_avg_reduction_ratio']}, "
            f"raw_full_turnover={row['raw_full_turnover_minutes']}, "
            f"stable_full_turnover={row['stable_full_turnover_minutes']}, "
            f"full_turnover_reduction={row['full_turnover_reduction_ratio']}"
        )
    lines.append("")

    lines.append("RAW MODEL SCORE SUMMARY")
    for row in report["raw_model_score_summary"]:
        lines.append(
            f"- {row['model_id']}: rows={row['row_count']}, avg={row['score_avg']}, "
            f"max={row['score_max']}, C+={row['c_or_better_count']} ({row['c_or_better_ratio']}), "
            f"D+={row['d_or_better_count']} ({row['d_or_better_ratio']})"
        )
    lines.append("")

    lines.append("STABLE MODEL SCORE SUMMARY")
    for row in report["stable_model_score_summary"]:
        lines.append(
            f"- {row['model_id']}: rows={row['row_count']}, avg={row['score_avg']}, "
            f"max={row['score_max']}, C+={row['c_or_better_count']} ({row['c_or_better_ratio']}), "
            f"D+={row['d_or_better_count']} ({row['d_or_better_ratio']})"
        )
    lines.append("")

    lines.append("STABLE REPEATED CANDIDATES TOP")
    for row in report["stable_repeated_candidates"]:
        lines.append(
            f"- {row['model_id']} {row['stock_code']} {row['stock_name']}: "
            f"appear={row['appear_count']}, best={row['best_grade']}, avg={row['avg_score']}, "
            f"first={row['first_minute']}, last={row['last_minute']}"
        )
    lines.append("")

    lines.append("RAW C OR BETTER")
    for row in report["raw_c_or_better"]:
        lines.append(
            f"- {row['replay_minute']} {row['model_id']} rank={row['candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} {row['grade_text']} score={row['score']} "
            f"tv={row['trade_value_eok']} strength={row['execution_strength']}"
        )
    lines.append("")

    lines.append("STABLE C OR BETTER")
    for row in report["stable_c_or_better"]:
        lines.append(
            f"- {row['replay_minute']} {row['model_id']} rank={row['candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} {row['grade_text']} score={row['score']} "
            f"tv={row['trade_value_eok']} strength={row['execution_strength']}"
        )
    lines.append("")

    lines.append("STABLE D OR BETTER TOP 60")
    for row in report["stable_d_or_better"][:60]:
        lines.append(
            f"- {row['replay_minute']} {row['model_id']} rank={row['candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} {row['grade_text']} score={row['score']} "
            f"raw_count={row['stability_recent_raw_count']} stable_count={row['stability_recent_stable_count']} "
            f"penalty={row['stability_penalty']}"
        )
    lines.append("")

    lines.append("STABLE SCORE TOP")
    for row in report["stable_score_top"]:
        lines.append(
            f"- {row['replay_minute']} {row['model_id']} rank={row['stable_candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} stable={row['stable_grade_text']} "
            f"stable_score={row['stable_score']} raw={row['candidate_grade_text']} raw_score={row['candidate_score']}"
        )
    lines.append("")

    lines.append("TRADE_METRIC_7 OBSERVATION")
    for row in report["trade_metric_7_observation"]:
        lines.append(
            f"- {row['mode']} {row['replay_minute']} {row['model_id']} rank={row['candidate_rank']} "
            f"{row['stock_code']} {row['stock_name']} score={row['score']} "
            f"m7_close={row['trade_metric_7_close']} "
            f"m7_delta={row['trade_metric_7_delta_in_min']} "
            f"m7_prev_delta={row['trade_metric_7_delta_from_prev_min']}"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def build_report(
    candidate_csv: Path,
    transition_csv: Path,
    stable_candidate_csv: Path,
    stable_transition_csv: Path,
    out_dir: Path,
) -> dict[str, Any]:
    raw_rows_all = read_csv(candidate_csv)
    raw_transition_rows_all = read_csv(transition_csv)
    stable_rows_all = read_csv(stable_candidate_csv)
    stable_transition_rows_all = read_csv(stable_transition_csv)

    raw_rows = focus_rows(raw_rows_all)
    stable_rows = focus_rows(stable_rows_all)
    raw_transitions = transition_summary(focus_rows(raw_transition_rows_all), mode="raw")
    stable_transitions = transition_summary(focus_rows(stable_transition_rows_all), mode="stable")

    raw_churn = churn_by_model(raw_transitions)
    stable_churn = churn_by_model(stable_transitions)

    raw_repeated = repeated_candidates(
        raw_rows,
        mode="raw",
        score_field="candidate_score",
        grade_field="candidate_grade_text",
        n=30,
    )
    stable_repeated = repeated_candidates(
        stable_rows,
        mode="stable",
        score_field="stable_score",
        grade_field="stable_grade_text",
        n=30,
    )

    report = {
        "inputs": {
            "candidate_csv": str(candidate_csv),
            "transition_csv": str(transition_csv),
            "stable_candidate_csv": str(stable_candidate_csv),
            "stable_transition_csv": str(stable_transition_csv),
            "raw_total_row_count": len(raw_rows_all),
            "stable_total_row_count": len(stable_rows_all),
            "raw_focus_row_count": len(raw_rows),
            "stable_focus_row_count": len(stable_rows),
            "focus_start_minute": FOCUS_START_MINUTE,
        },
        "raw_model_score_summary": model_score_summary(
            raw_rows,
            mode="raw",
            score_field="candidate_score",
            grade_field="candidate_grade_text",
        ),
        "stable_model_score_summary": model_score_summary(
            stable_rows,
            mode="stable",
            score_field="stable_score",
            grade_field="stable_grade_text",
        ),
        "raw_grade_summary": model_grade_summary(
            raw_rows,
            mode="raw",
            grade_field="candidate_grade_text",
        ),
        "stable_grade_summary": model_grade_summary(
            stable_rows,
            mode="stable",
            grade_field="stable_grade_text",
        ),
        "raw_repeated_candidates": raw_repeated,
        "stable_repeated_candidates": stable_repeated,
        "raw_c_or_better": c_or_better_rows(
            raw_rows,
            mode="raw",
            rank_field="candidate_rank",
            score_field="candidate_score",
            grade_field="candidate_grade_text",
        ),
        "stable_c_or_better": c_or_better_rows(
            stable_rows,
            mode="stable",
            rank_field="stable_candidate_rank",
            score_field="stable_score",
            grade_field="stable_grade_text",
        ),
        "stable_d_or_better": d_or_better_rows(
            stable_rows,
            mode="stable",
            rank_field="stable_candidate_rank",
            score_field="stable_score",
            grade_field="stable_grade_text",
        ),
        "raw_transition_summary": raw_transitions,
        "stable_transition_summary": stable_transitions,
        "raw_churn_by_model": raw_churn,
        "stable_churn_by_model": stable_churn,
        "raw_vs_stable_churn": raw_vs_stable_churn(raw_churn, stable_churn),
        "stable_score_top": top_stable_scores(stable_rows, n=30),
        "raw_timeline_for_repeated_candidates": timeline_by_repeated_top(
            raw_rows,
            raw_repeated,
            mode="raw",
            rank_field="candidate_rank",
            score_field="candidate_score",
            grade_field="candidate_grade_text",
            limit_stocks=10,
        ),
        "stable_timeline_for_repeated_candidates": timeline_by_repeated_top(
            stable_rows,
            stable_repeated,
            mode="stable",
            rank_field="stable_candidate_rank",
            score_field="stable_score",
            grade_field="stable_grade_text",
            limit_stocks=10,
        ),
        "trade_metric_7_observation": (
            trade_metric_7_observation(
                raw_rows,
                mode="raw",
                score_field="candidate_score",
                grade_field="candidate_grade_text",
                n=15,
            )
            + trade_metric_7_observation(
                stable_rows,
                mode="stable",
                score_field="stable_score",
                grade_field="stable_grade_text",
                n=15,
            )
        ),
        "notes": {
            "focus_window": f"Rows with replay_minute >= {FOCUS_START_MINUTE}.",
            "raw": "Original model Top5.",
            "stable": "Replay-only stability overlay Top5.",
            "trade_metric_7": "Unresolved diagnostic metric. Observe movement only; do not treat as scoring input yet.",
            "grades": "Uses restored legacy bands A91/B81/C71/D61/F.",
        },
    }

    json_path = out_dir / "replay_candidate_report_latest.json"
    txt_path = out_dir / "replay_candidate_report_latest.txt"
    raw_repeated_csv = out_dir / "replay_candidate_report_repeated_latest.csv"
    stable_repeated_csv = out_dir / "replay_candidate_report_stable_repeated_latest.csv"
    raw_cplus_csv = out_dir / "replay_candidate_report_c_or_better_latest.csv"
    stable_cplus_csv = out_dir / "replay_candidate_report_stable_c_or_better_latest.csv"
    stable_dplus_csv = out_dir / "replay_candidate_report_stable_d_or_better_latest.csv"
    churn_compare_csv = out_dir / "replay_candidate_report_raw_vs_stable_churn_latest.csv"
    stable_score_top_csv = out_dir / "replay_candidate_report_stable_score_top_latest.csv"
    metric7_csv = out_dir / "replay_candidate_report_trade_metric_7_latest.csv"
    raw_timeline_csv = out_dir / "replay_candidate_report_timeline_latest.csv"
    stable_timeline_csv = out_dir / "replay_candidate_report_stable_timeline_latest.csv"

    report["outputs"] = {
        "json": str(json_path),
        "txt": str(txt_path),
        "raw_repeated_csv": str(raw_repeated_csv),
        "stable_repeated_csv": str(stable_repeated_csv),
        "raw_c_or_better_csv": str(raw_cplus_csv),
        "stable_c_or_better_csv": str(stable_cplus_csv),
        "stable_d_or_better_csv": str(stable_dplus_csv),
        "raw_vs_stable_churn_csv": str(churn_compare_csv),
        "stable_score_top_csv": str(stable_score_top_csv),
        "trade_metric_7_csv": str(metric7_csv),
        "raw_timeline_csv": str(raw_timeline_csv),
        "stable_timeline_csv": str(stable_timeline_csv),
    }

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report_txt(txt_path, report)

    repeated_fields = [
        "mode",
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
    ]
    write_csv(raw_repeated_csv, report["raw_repeated_candidates"], repeated_fields)
    write_csv(stable_repeated_csv, report["stable_repeated_candidates"], repeated_fields)

    candidate_fields = [
        "mode",
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "score",
        "grade_text",
        "trade_value_eok",
        "execution_strength",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "trade_metric_7_delta_from_prev_min",
        "bid_ask_ratio",
        "program_net",
    ]
    write_csv(raw_cplus_csv, report["raw_c_or_better"], candidate_fields)
    write_csv(stable_cplus_csv, report["stable_c_or_better"], candidate_fields)

    write_csv(stable_dplus_csv, report["stable_d_or_better"], [
        "mode",
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "score",
        "grade_text",
        "trade_value_eok",
        "execution_strength",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "stable_prev",
        "stability_recent_raw_count",
        "stability_recent_stable_count",
        "stability_penalty",
    ])
    write_csv(churn_compare_csv, report["raw_vs_stable_churn"], [
        "model_id",
        "raw_entered_avg",
        "stable_entered_avg",
        "entered_avg_reduction_ratio",
        "raw_exited_avg",
        "stable_exited_avg",
        "raw_full_turnover_minutes",
        "stable_full_turnover_minutes",
        "full_turnover_reduction_ratio",
        "raw_entered_total",
        "stable_entered_total",
        "raw_exited_total",
        "stable_exited_total",
    ])
    write_csv(stable_score_top_csv, report["stable_score_top"], [
        "replay_minute",
        "model_id",
        "stable_candidate_rank",
        "stock_code",
        "stock_name",
        "stable_score",
        "stable_grade_text",
        "candidate_score",
        "candidate_grade_text",
        "trade_value_eok",
        "execution_strength",
        "stability_recent_raw_count",
        "stability_recent_stable_count",
        "stability_prev_stable",
        "stability_penalty",
    ])
    write_csv(metric7_csv, report["trade_metric_7_observation"], [
        "mode",
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "score",
        "grade_text",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "trade_metric_7_delta_from_prev_min",
        "execution_strength",
        "trade_value_eok",
    ])
    timeline_fields = [
        "mode",
        "replay_minute",
        "model_id",
        "candidate_rank",
        "stock_code",
        "stock_name",
        "score",
        "grade_text",
        "trade_value_eok",
        "execution_strength",
        "trade_metric_7_close",
        "trade_metric_7_delta_in_min",
        "bid_ask_ratio",
    ]
    write_csv(raw_timeline_csv, report["raw_timeline_for_repeated_candidates"], timeline_fields)
    write_csv(stable_timeline_csv, report["stable_timeline_for_repeated_candidates"], timeline_fields)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate raw-vs-stable reports from StockBoard candidate replay CSVs.")
    parser.add_argument("--out-dir", default="data/runtime/replay_output")
    parser.add_argument("--candidate-csv", default=None)
    parser.add_argument("--transition-csv", default=None)
    parser.add_argument("--stable-candidate-csv", default=None)
    parser.add_argument("--stable-transition-csv", default=None)
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate_csv = Path(args.candidate_csv) if args.candidate_csv else latest_file(
        out_dir,
        "*_candidate_replay_*_top5.csv",
        exclude=("_stable_",),
    )
    transition_csv = Path(args.transition_csv) if args.transition_csv else latest_file(
        out_dir,
        "*_candidate_replay_*_transitions.csv",
        exclude=("_stable_",),
    )
    stable_candidate_csv = Path(args.stable_candidate_csv) if args.stable_candidate_csv else latest_file(
        out_dir,
        "*_candidate_replay_*_stable_top5.csv",
    )
    stable_transition_csv = Path(args.stable_transition_csv) if args.stable_transition_csv else latest_file(
        out_dir,
        "*_candidate_replay_*_stable_transitions.csv",
    )

    report = build_report(
        candidate_csv=candidate_csv,
        transition_csv=transition_csv,
        stable_candidate_csv=stable_candidate_csv,
        stable_transition_csv=stable_transition_csv,
        out_dir=out_dir,
    )

    print("REPLAY_CANDIDATE_REPORT_OK")
    print("candidate_csv:", report["inputs"]["candidate_csv"])
    print("stable_candidate_csv:", report["inputs"]["stable_candidate_csv"])
    print("transition_csv:", report["inputs"]["transition_csv"])
    print("stable_transition_csv:", report["inputs"]["stable_transition_csv"])
    print("raw_focus_row_count:", report["inputs"]["raw_focus_row_count"])
    print("stable_focus_row_count:", report["inputs"]["stable_focus_row_count"])
    print("raw_model_score_summary:", report["raw_model_score_summary"])
    print("stable_model_score_summary:", report["stable_model_score_summary"])
    print("raw_vs_stable_churn:", report["raw_vs_stable_churn"])
    print("raw_c_or_better_count:", len(report["raw_c_or_better"]))
    print("stable_c_or_better_count:", len(report["stable_c_or_better"]))
    print("stable_d_or_better_count:", len(report["stable_d_or_better"]))
    print("stable_repeated_top5:", report["stable_repeated_candidates"][:5])
    print("outputs:", report["outputs"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())