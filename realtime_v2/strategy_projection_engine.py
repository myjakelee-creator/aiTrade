from __future__ import annotations

import os
from collections import Counter
from typing import Any

from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.board_projection_runtime import LatestOnlyProjectionWorker
from realtime_v2.common import normalize_code, now_text


_LANE_ORDER = ("focus", "ready", "watch", "wait_data", "weak")
_LANE_LABELS = {
    "focus": "FOCUS",
    "ready": "READY",
    "watch": "WATCH",
    "wait_data": "WAIT_DATA",
    "weak": "WEAK",
}
_LANE_PRIORITY = {name: index for index, name in enumerate(_LANE_ORDER)}


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _strategy_lane(row: dict[str, Any]) -> str:
    status = str(row.get("candidate_status") or "").strip().upper()
    coverage = _number(row.get("candidate_score_coverage"))
    required_missing = row.get("required_feature_missing")
    has_required_missing = bool(required_missing) if isinstance(required_missing, list) else False

    if status == "WAIT_DATA" or has_required_missing or (
        coverage is not None and coverage < 0.60
    ):
        return "wait_data"
    if bool(row.get("is_candidate")) or (
        (_integer(row.get("candidate_rank")) or 999999) <= 5
    ):
        return "focus"
    if status == "READY":
        return "ready"
    if status in {"WATCH", "EARLY"}:
        return "watch"
    return "weak"


def _row_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    lane = _strategy_lane(row)
    return (
        _LANE_PRIORITY.get(lane, 999),
        -(_number(row.get("focus_score")) or 0.0),
        -(_number(row.get("candidate_score")) or 0.0),
        -(_number(row.get("confirmation_score")) or 0.0),
        -(_number(row.get("entry_score")) or 0.0),
        _integer(row.get("model_rank")) or 999999,
        normalize_code(row.get("stock_code")) or "999999",
    )


def _project_row(row: dict[str, Any], lane: str) -> dict[str, Any]:
    return {
        "stock_code": normalize_code(row.get("stock_code")),
        "stock_name": row.get("stock_name"),
        "price": row.get("price"),
        "change_rate": row.get("change_rate"),
        "trade_value_eok": row.get("trade_value_eok"),
        "received_at": row.get("received_at"),
        "price_age_sec": row.get("price_age_sec"),
        "row_source": row.get("row_source"),
        "candidate_model_id": row.get("candidate_model_id"),
        "candidate_model_name": row.get("candidate_model_name"),
        "candidate_score": row.get("candidate_score"),
        "candidate_grade": row.get("candidate_grade"),
        "candidate_grade_text": row.get("candidate_grade_text"),
        "candidate_status": row.get("candidate_status"),
        "candidate_score_coverage": row.get("candidate_score_coverage"),
        "entry_score": row.get("entry_score"),
        "confirmation_score": row.get("confirmation_score"),
        "focus_score": row.get("focus_score"),
        "model_rank": row.get("model_rank"),
        "pool_stage": row.get("pool_stage"),
        "candidate_rank": row.get("candidate_rank"),
        "is_candidate": bool(row.get("is_candidate")),
        "required_feature_missing": list(row.get("required_feature_missing") or []),
        "grade_guard_failures": list(row.get("grade_guard_failures") or []),
        "momentum": row.get("momentum"),
        "candidate_reason": row.get("candidate_reason"),
        "strategy_lane": lane,
        "strategy_lane_label": _LANE_LABELS[lane],
        # StrategyBoard is a read-only decision projection. It never creates an
        # order instruction or re-scores the candidate model.
        "order_side": None,
        "auto_order_allowed": False,
    }


class StrategyProjectionBuilder:
    """Project one selected candidate model into StrategyBoard decision lanes.

    Input rows are the already-completed shared feature/candidate snapshot. This
    builder only classifies and compacts those results; it performs no TR request,
    no FeatureSnapshot construction, and no candidate-score calculation.
    """

    def __init__(self, max_rows: int | None = None) -> None:
        self.max_rows = max_rows or _env_int(
            "STOCKBOARD_STRATEGY_PROJECTION_LIMIT", 50, 5, 300
        )

    def __call__(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        _meta: dict[str, Any],
    ) -> dict[str, Any]:
        source_rows = [row for row in rows if isinstance(row, dict)]
        if not source_rows:
            return {
                "schema_version": 1,
                "source": "strategy_projection_engine",
                "status": "WAIT_FEATURE_ROWS",
                "ts": now_text(),
                "input_feature_version": feature_version,
                "feature_row_count": 0,
                "row_count": 0,
                "lane_order": list(_LANE_ORDER),
                "lane_counts": {name: 0 for name in _LANE_ORDER},
                "rows": [],
                "policy": self._policy(),
            }

        ordered = sorted(source_rows, key=_row_sort_key)
        internal_lane_counts = Counter(_strategy_lane(row) for row in ordered)
        selected = ordered[: self.max_rows]
        output_lane_counts = Counter(_strategy_lane(row) for row in selected)
        lane_rank: Counter[str] = Counter()
        projected: list[dict[str, Any]] = []

        for strategy_rank, row in enumerate(selected, start=1):
            lane = _strategy_lane(row)
            lane_rank[lane] += 1
            item = _project_row(row, lane)
            item["strategy_rank"] = strategy_rank
            item["strategy_lane_rank"] = lane_rank[lane]
            projected.append(item)

        model_ids = sorted(
            {
                str(row.get("candidate_model_id") or "").strip()
                for row in source_rows
                if str(row.get("candidate_model_id") or "").strip()
            }
        )
        model_names = sorted(
            {
                str(row.get("candidate_model_name") or "").strip()
                for row in source_rows
                if str(row.get("candidate_model_name") or "").strip()
            }
        )

        return {
            "schema_version": 1,
            "source": "strategy_projection_engine",
            "status": "READY",
            "ts": now_text(),
            "input_feature_version": feature_version,
            "feature_row_count": len(source_rows),
            "row_count": len(projected),
            "output_limit": self.max_rows,
            "selected_candidate_model_id": model_ids[0] if len(model_ids) == 1 else None,
            "selected_candidate_model_name": (
                model_names[0] if len(model_names) == 1 else None
            ),
            "candidate_model_ids": model_ids,
            "lane_order": list(_LANE_ORDER),
            "lane_labels": dict(_LANE_LABELS),
            "lane_counts": {
                name: int(internal_lane_counts.get(name, 0)) for name in _LANE_ORDER
            },
            "output_lane_counts": {
                name: int(output_lane_counts.get(name, 0)) for name in _LANE_ORDER
            },
            "rows": projected,
            "policy": self._policy(),
        }

    @staticmethod
    def _policy() -> dict[str, Any]:
        return {
            "direct_tr_allowed": False,
            "feature_recalculation_allowed": False,
            "candidate_rescore_allowed": False,
            "selected_candidate_model_only": True,
            "input": "board_data_hub_shared_feature_snapshot",
            "queue": "latest_only_depth_1",
            "html_calculation_allowed": False,
            "auto_order_allowed": False,
        }


class StrategyProjectionRuntime:
    def __init__(self, hub: BoardDataHub, min_interval_ms: int = 500) -> None:
        self.worker = LatestOnlyProjectionWorker(
            name="strategy",
            hub=hub,
            builder=StrategyProjectionBuilder(),
            min_interval_ms=min_interval_ms,
        )

    def start(self) -> None:
        self.worker.start()

    def submit(self, feature_version: int) -> None:
        self.worker.submit(feature_version)

    def stop(self) -> None:
        self.worker.stop()

    def status(self) -> dict[str, Any]:
        return self.worker.status()
