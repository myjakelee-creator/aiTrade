from __future__ import annotations

from typing import Any


POLICY = "grade_score_unified_v1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _source_rank(row: dict[str, Any]) -> int:
    try:
        value = int(row.get("_source_rank") or row.get("trade_value_rank") or row.get("rank") or 999999)
    except (TypeError, ValueError):
        value = 999999
    return value if value > 0 else 999999


def _score(row: dict[str, Any]) -> float:
    for key in ("grade_score", "candidate_score", "score_percent"):
        if row.get(key) not in (None, ""):
            return _number(row.get(key))
    return 0.0


def unified_apply_funnel(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use the final grade score as the only candidate ordering value.

    Entry/confirmation/focus scores remain available as explanation fields, but they
    no longer create separate Top50/Top20/Top5 orderings.  This makes the displayed
    grade, candidate score, and model order monotonic by construction.
    """

    funnel = self.config.get("funnel") if isinstance(self.config.get("funnel"), dict) else {}
    top20_take = int((funnel.get("top20") or {}).get("take") or 20)
    top50_take = int((funnel.get("top50") or {}).get("take") or 50)
    top5_take = int((funnel.get("top5") or {}).get("take") or 5)

    for row in rows:
        score = round(_score(row), 2)
        row["candidate_score"] = score
        row["grade_score"] = score
        row["score_percent"] = score
        row["selection_score"] = score
        row["selection_order_policy"] = POLICY

    ordered = sorted(
        rows,
        key=lambda row: (
            row.get("candidate_status") == "WAIT_DATA",
            -_score(row),
            -_number(row.get("candidate_score_coverage")),
            _source_rank(row),
            str(row.get("stock_code") or ""),
        ),
    )

    for rank, row in enumerate(ordered, start=1):
        row["selection_rank"] = rank
        row["model_rank"] = rank
        row["pool_rank"] = rank
        row["funnel_rank"] = rank

        # Compatibility ranks now refer to the same unified order.  The component
        # scores themselves remain untouched for diagnostics and score breakdowns.
        row["entry_rank"] = rank
        row["confirmation_rank"] = rank
        row["focus_rank"] = rank

        if rank <= top20_take:
            target_lane = "hot"
            pool_stage = "top20"
        elif rank <= top50_take:
            target_lane = "warm"
            pool_stage = "top50"
        else:
            target_lane = "cold"
            pool_stage = "top300"

        row["target_lane"] = target_lane
        row["pool_stage"] = pool_stage
        row["is_candidate"] = rank <= top5_take and row.get("candidate_status") != "WAIT_DATA"
        row["candidate_rank"] = rank if row["is_candidate"] else None
        row["desired_top20"] = rank <= top20_take and row.get("candidate_status") != "WAIT_DATA"

    return ordered


def install() -> None:
    import stockboard_candidate_engine as engine

    ranking_class = engine.ConfigDrivenCandidateRankingEngine
    if getattr(ranking_class, "_stockboard_grade_score_unified_installed", False):
        return

    ranking_class._apply_funnel = unified_apply_funnel
    ranking_class._stockboard_grade_score_unified_installed = True
