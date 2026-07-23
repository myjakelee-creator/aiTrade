from __future__ import annotations

"""Project closed-session fields on the final API snapshot.

Installed after opening-burst cache, so this is the last server-side projection
seen by HTTP/SSE callers. It does not touch Collector, QAx, FIDs, trade apply,
sorting, cache cadence, or browser calculations.
"""

from typing import Any

from realtime_v2.market_session import market_session_now

PATCH_VERSION = "final_closed_snapshot_projection_v1"
CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}
_MARKER = "_stockboard_final_closed_snapshot_projection_wrapper"


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _grade_for_rank(rank: Any) -> tuple[str | None, int | None]:
    value = _number(rank)
    if value is None:
        return None, None
    position = int(value)
    if position < 1 or position > 100:
        return "F", 0
    score = 101 - position
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"
    return grade, score


def _project_row(row: dict[str, Any]) -> tuple[int, int, int, int]:
    amount_done = 0
    grade_done = 0
    rank_gap_done = 0
    minute_hidden = 0

    current = _number(row.get("trade_value_eok"))
    previous = _number(row.get("prev_trade_value_eok"))
    if _number(row.get("amount_ratio")) is None and current is not None and current >= 0 and previous is not None and previous >= 1:
        row["amount_ratio"] = round(current / previous, 4)
        row["amount_ratio_status"] = "final_snapshot_completed"
        row["amount_ratio_source"] = "current_div_previous_final_snapshot"
        amount_done = 1
    elif previous is not None and previous < 1:
        row["amount_ratio_status"] = "previous_value_below_plausibility_floor"
        row["amount_ratio_missing_reason"] = "previous_value_below_plausibility_floor"

    display_rank = row.get("rank") or row.get("displayed_rank")
    grade, score = _grade_for_rank(display_rank)
    if grade is not None:
        row["grade"] = grade
        row["candidate_grade"] = grade
        row["grade_score"] = score
        row["candidate_score"] = score
        row["grade_text"] = f"{grade}{score}"
        row["grade_display_source"] = "FIVE_FACTOR_FLOW_V01_rank_only"
        grade_done = 1

    today_original = _number(row.get("original_rank") or row.get("seed_rank"))
    previous_original = _number(
        row.get("previous_original_rank")
        or row.get("prev_original_rank")
        or row.get("prev_rank")
    )
    if today_original is not None and previous_original is not None:
        gap = int(previous_original) - int(today_original)
        row["rank_diff"] = gap
        row["rank_change"] = gap
        row["rank_gap_source"] = "original_rank_vs_previous_original_rank"
        rank_gap_done = 1

    status = str(row.get("one_min_status") or row.get("minute_trade_value_status") or "").strip().lower()
    proven = {"ok", "new", "complete", "completed", "exact", "exact_live", "same_session_last_valid"}
    if status not in proven:
        changed = False
        for key in ("one_min_trade_value_eok", "trade_value_1m_eok", "minute_trade_value_eok"):
            if _number(row.get(key)) == 0:
                row.pop(key, None)
                changed = True
        if changed:
            row["one_min_status"] = "unavailable_after_restart_without_completed_bucket"
            row["minute_trade_value_status"] = row["one_min_status"]
            row["one_min_available"] = False
            minute_hidden = 1

    return amount_done, grade_done, rank_gap_done, minute_hidden


def install(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None:
        return
    current_snapshot = state_class.snapshot
    if getattr(current_snapshot, _MARKER, False):
        return
    original_snapshot = current_snapshot

    def snapshot(self, *args, **kwargs):
        payload = original_snapshot(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        phase = str(getattr(market_session_now(), "phase", "") or "")
        if phase not in CLOSED_PHASES:
            return payload
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return payload

        amount_count = grade_count = rank_gap_count = minute_hidden_count = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            a, g, r, m = _project_row(row)
            amount_count += a
            grade_count += g
            rank_gap_count += r
            minute_hidden_count += m

        values = {
            "final_closed_snapshot_projection_version": PATCH_VERSION,
            "final_closed_snapshot_projection_phase": phase,
            "final_closed_amount_ratio_count": amount_count,
            "final_closed_grade_count": grade_count,
            "final_closed_rank_gap_count": rank_gap_count,
            "final_closed_one_min_hidden_count": minute_hidden_count,
        }
        status = payload.setdefault("status", {})
        if isinstance(status, dict):
            status.update(values)
        lock = getattr(self, "lock", None)
        state_status = getattr(self, "status", None)
        if lock is not None and isinstance(state_status, dict):
            with lock:
                state_status.update(values)
        return payload

    setattr(snapshot, _MARKER, True)
    state_class.snapshot = snapshot
    state_class._stockboard_final_closed_snapshot_projection_installed = True
