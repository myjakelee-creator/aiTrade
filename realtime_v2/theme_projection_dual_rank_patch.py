from __future__ import annotations

import time
from typing import Any, Callable


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _weighted_score(components: list[tuple[float | None, float]]) -> tuple[float, float]:
    usable = [
        (max(0.0, min(100.0, float(value))), float(weight))
        for value, weight in components
        if value is not None and float(weight) > 0
    ]
    active_weight = sum(weight for _value, weight in usable)
    if active_weight <= 0:
        return 0.0, 0.0
    score = sum(value * weight for value, weight in usable) / active_weight
    return score, active_weight


def _coverage_cap(score: float, coverage: float, active_count: int) -> float:
    if coverage < 40:
        score = min(score, 59.0)
    elif coverage < 60:
        score = min(score, 69.0)
    elif coverage < 80:
        score = min(score, 79.0)
    if active_count <= 1:
        score = min(score, 69.0)
    return score


def _tie_aware_rank_percent(values: dict[str, float]) -> dict[str, float]:
    """Return dense percentile ranks while giving equal values equal scores.

    The previous generic ordinal rank used the theme id as a tie breaker. Two themes
    with identical market metrics could therefore receive very different scores,
    which also made an unrelated candidate-score test appear to influence Theme rank.
    """

    if not values:
        return {}
    unique_values = sorted({float(value) for value in values.values()}, reverse=True)
    if len(unique_values) == 1:
        return {key: 100.0 for key in values}
    denominator = len(unique_values) - 1
    by_value = {
        value: round((denominator - index) / denominator * 100.0, 4)
        for index, value in enumerate(unique_values)
    }
    return {key: by_value[float(value)] for key, value in values.items()}


def install(theme_module) -> None:
    """Publish server-completed momentum and money ranking views.

    The patch consumes only completed Theme summary rows. It performs small
    O(theme_count log theme_count) sorts, makes no TR/OpenAPI calls, does not
    rescore stocks, and leaves HTML responsible only for choosing which already
    sorted server view to display.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_dual_rank_installed", False):
        return

    original_call = builder_class.__call__

    def rank(values: dict[str, float]) -> dict[str, float]:
        return _tie_aware_rank_percent(values)

    def metric_rank(
        valid_rows: list[dict[str, Any]],
        key: str,
        transform: Callable[[dict[str, Any]], float | None] | None = None,
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        for row in valid_rows:
            theme_id = str(row.get("theme_id") or "")
            value = transform(row) if transform is not None else _number(row.get(key))
            if theme_id and value is not None:
                values[theme_id] = float(value)
        return rank(values)

    def flow_strength(row: dict[str, Any]) -> float | None:
        # Previous-session held metrics remain visible but are excluded from
        # ranking confirmation. A partially held theme is treated conservatively.
        if int(row.get("held_member_count") or 0) > 0:
            return None
        program = _number(row.get("program_net_eok"))
        large = _number(row.get("large_trade_net_eok"))
        if program is None and large is None:
            return None
        return float(program or 0.0) * 2.0 + float(large or 0.0) * 10.0

    def trend_state(row: dict[str, Any], score: float) -> str:
        if str(row.get("coverage_status") or "") == "WAIT_DATA":
            return "WAIT_DATA"
        average = _number(row.get("avg_change_rate"))
        one = _number(row.get("change_momentum_1m"))
        five = _number(row.get("change_persistence_5m"))
        breadth = _number(row.get("breadth_pct")) or 0.0
        if average is None:
            return "WAIT_DATA"
        if average <= 0 or (one is not None and one < -0.15):
            return "COOLING"
        if score >= 85 and (
            breadth >= 70
            or (one is not None and one >= 0.20)
            or (five is not None and five >= 0.30)
        ):
            return "SURGE"
        if score >= 70:
            return "RISING"
        return "STEADY"

    def money_state(row: dict[str, Any], score: float) -> str:
        if str(row.get("coverage_status") or "") == "WAIT_DATA":
            return "WAIT_DATA"
        ratio = _number(row.get("theme_amount_ratio"))
        one = _number(row.get("trade_value_1m_eok"))
        if score >= 85 and ratio is not None and ratio >= 1.5:
            return "SURGE"
        if score >= 70:
            return "RISING"
        if ratio is not None and ratio < 1.0 and (one is None or one <= 0):
            return "COOLING"
        return "STEADY"

    def apply_grade_state(
        row: dict[str, Any],
        prefix: str,
        score: float,
        state: str,
    ) -> None:
        grade = theme_module._grade(score)
        row[f"{prefix}_score"] = round(score, 2)
        row[f"{prefix}_score_text"] = f"{score:.1f}"
        row[f"{prefix}_grade"] = grade
        row[f"{prefix}_grade_class"] = f"grade-{grade.lower()}"
        row[f"{prefix}_state"] = state
        row[f"{prefix}_state_text"] = state
        row[f"{prefix}_state_class"] = state.lower().replace("_", "-")

    def apply_fund_flow_bar(
        row: dict[str, Any],
        *,
        prefix: str,
        recent_value: float | None,
        recent_rank: float | None,
        ratio_rank: float | None,
        positive_flow_rank: float | None,
        coverage: float,
        active_count: int,
    ) -> None:
        ratio = _number(row.get("theme_amount_ratio"))
        ratio_absolute = (
            max(0.0, min(100.0, float(ratio) * 20.0))
            if ratio is not None and ratio > 0 and ratio_rank is not None
            else 0.0
        )
        if recent_value is None or recent_value <= 0:
            score = 0.0
            active_weight = 0.0
        else:
            raw_score, active_weight = _weighted_score(
                [
                    (recent_rank, 50.0),
                    (ratio_rank, 20.0),
                    (ratio_absolute, 20.0),
                    (positive_flow_rank, 10.0),
                ]
            )
            score = _coverage_cap(raw_score, coverage, active_count)
        score = round(max(0.0, min(100.0, score)), 2)
        row[f"fund_flow_{prefix}_score"] = score
        row[f"fund_flow_{prefix}_bar_pct"] = score
        row[f"fund_flow_{prefix}_text"] = f"{score:.0f}"
        row[f"fund_flow_{prefix}_metric_weight"] = round(active_weight, 2)

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        total_started = time.perf_counter()
        payload = original_call(self, feature_version, rows, meta)
        if not isinstance(payload, dict) or payload.get("status") != "READY":
            return payload
        summaries = payload.get("rows")
        if not isinstance(summaries, list):
            return payload

        started = time.perf_counter()
        valid_rows = [row for row in summaries if isinstance(row, dict)]
        average_rank = metric_rank(valid_rows, "avg_change_rate")
        momentum_rank = metric_rank(valid_rows, "change_momentum_1m")
        persistence_rank = metric_rank(valid_rows, "change_persistence_5m")
        amount_ratio_rank = metric_rank(valid_rows, "theme_amount_ratio")
        one_money_rank = metric_rank(valid_rows, "trade_value_1m_eok")
        five_money_rank = metric_rank(valid_rows, "trade_value_5m_eok")
        flow_rank = metric_rank(valid_rows, "", flow_strength)

        for row in valid_rows:
            theme_id = str(row.get("theme_id") or "")
            coverage = float(_number(row.get("coverage_pct")) or 0.0)
            active_count = int(row.get("active_member_count") or 0)
            average = _number(row.get("avg_change_rate"))
            breadth = _number(row.get("breadth_pct"))
            ratio_count = int(row.get("amount_ratio_member_count") or 0)
            ratio_value = (
                amount_ratio_rank.get(theme_id)
                if ratio_count >= min(2, max(1, active_count))
                else None
            )

            trend_raw, trend_weight = _weighted_score(
                [
                    (average_rank.get(theme_id), 35.0),
                    (breadth, 20.0),
                    (momentum_rank.get(theme_id), 15.0),
                    (persistence_rank.get(theme_id), 15.0),
                    (ratio_value, 10.0),
                    (flow_rank.get(theme_id), 5.0),
                ]
            )
            trend_score = _coverage_cap(trend_raw, coverage, active_count)
            if average is None or average <= 0:
                trend_score = min(trend_score, 59.0)
            elif (breadth or 0.0) < 50.0:
                trend_score = min(trend_score, 79.0)
            trend_score = round(max(0.0, min(100.0, trend_score)), 2)
            apply_grade_state(row, "trend", trend_score, trend_state(row, trend_score))
            row["trend_metric_weight"] = round(trend_weight, 2)

            money_raw, money_weight = _weighted_score(
                [
                    (ratio_value, 40.0),
                    (one_money_rank.get(theme_id), 25.0),
                    (five_money_rank.get(theme_id), 20.0),
                    (flow_rank.get(theme_id), 10.0),
                    (coverage, 5.0),
                ]
            )
            money_score = round(
                max(
                    0.0,
                    min(100.0, _coverage_cap(money_raw, coverage, active_count)),
                ),
                2,
            )
            apply_grade_state(row, "money", money_score, money_state(row, money_score))
            row["money_metric_weight"] = round(money_weight, 2)
            row["flow_confirmation_rank_score"] = flow_rank.get(theme_id)

            raw_flow = flow_strength(row)
            positive_flow_rank = (
                flow_rank.get(theme_id)
                if raw_flow is not None and raw_flow > 0
                else 0.0
            )
            apply_fund_flow_bar(
                row,
                prefix="1m",
                recent_value=_number(row.get("trade_value_1m_eok")),
                recent_rank=(
                    one_money_rank.get(theme_id)
                    if (_number(row.get("trade_value_1m_eok")) or 0.0) > 0
                    else 0.0
                ),
                ratio_rank=ratio_value,
                positive_flow_rank=positive_flow_rank,
                coverage=coverage,
                active_count=active_count,
            )
            apply_fund_flow_bar(
                row,
                prefix="5m",
                recent_value=_number(row.get("trade_value_5m_eok")),
                recent_rank=(
                    five_money_rank.get(theme_id)
                    if (_number(row.get("trade_value_5m_eok")) or 0.0) > 0
                    else 0.0
                ),
                ratio_rank=ratio_value,
                positive_flow_rank=positive_flow_rank,
                coverage=coverage,
                active_count=active_count,
            )

        trend_rows = sorted(
            valid_rows,
            key=lambda row: (
                -float(row.get("trend_score") or 0.0),
                -float(row.get("avg_change_rate") or -999.0),
                -float(row.get("breadth_pct") or 0.0),
                -float(row.get("theme_amount_ratio") or 0.0),
                str(row.get("theme_name") or ""),
            ),
        )
        money_rows = sorted(
            valid_rows,
            key=lambda row: (
                -float(row.get("money_score") or 0.0),
                -float(row.get("theme_amount_ratio") or 0.0),
                -float(row.get("trade_value_1m_eok") or 0.0),
                -float(row.get("trade_value_5m_eok") or 0.0),
                str(row.get("theme_name") or ""),
            ),
        )

        for index, row in enumerate(trend_rows, start=1):
            row["trend_rank"] = index
            row["trend_display_rank"] = index
            # Momentum is the default ThemeBoard view; legacy aliases remain
            # display-compatible with the existing HTML until its UI patch loads.
            row["rank"] = index
            row["display_rank"] = index
            row["score"] = row.get("trend_score")
            row["score_text"] = row.get("trend_score_text")
            row["grade"] = row.get("trend_grade")
            row["grade_class"] = row.get("trend_grade_class")
            row["state"] = row.get("trend_state")
            row["state_text"] = row.get("trend_state_text")
            row["state_class"] = row.get("trend_state_class")

        for index, row in enumerate(money_rows, start=1):
            row["money_rank"] = index
            row["money_display_rank"] = index

        payload["rows"] = trend_rows
        payload["money_rows"] = [dict(row) for row in money_rows]
        payload["ranking_views"] = {
            "default": "momentum",
            "available": ["momentum", "money"],
            "momentum_label": "상승탄력",
            "money_label": "돈쏠림",
            "browser_sort_allowed": False,
        }
        payload["ranking_policy"] = {
            "momentum": {
                "average_change_rank": 35,
                "breadth": 20,
                "change_momentum_1m_rank": 15,
                "change_persistence_5m_rank": 15,
                "theme_amount_ratio_rank": 10,
                "program_large_trade_confirmation_rank": 5,
                "nonpositive_average_grade_cap": "F59",
            },
            "money": {
                "theme_amount_ratio_rank": 40,
                "trade_value_1m_rank": 25,
                "trade_value_5m_rank": 20,
                "program_large_trade_confirmation_rank": 10,
                "coverage": 5,
            },
            "fund_flow_bars": {
                "server_completed": True,
                "recent_money_relative_rank": 50,
                "amount_ratio_relative_rank": 20,
                "amount_ratio_absolute_signal": 20,
                "positive_program_large_trade_rank": 10,
                "zero_recent_money_score": 0,
                "browser_calculation_allowed": False,
            },
            "average_candidate_score_used": False,
            "amount_ratio_applied_once_per_view": True,
            "tie_policy": "equal_values_equal_percentile",
        }

        # Refresh the selected-detail summary cache with the completed dual-rank
        # fields. No member rows are built here.
        lock = getattr(self, "_theme_summary_split_lock", None)
        if lock is not None:
            with lock:
                self._theme_latest_summary_by_id = {
                    str(row.get("theme_id") or ""): dict(row) for row in trend_rows
                }

        dual_rank_ms = (time.perf_counter() - started) * 1000.0
        total_ms = (time.perf_counter() - total_started) * 1000.0
        performance = payload.setdefault("performance_breakdown", {})
        if isinstance(performance, dict):
            performance["dual_rank_ms"] = round(dual_rank_ms, 3)
            performance["total_ms"] = round(total_ms, 3)
            aggregate = float(performance.get("aggregate_ms") or 0.0)
            score_sort = float(performance.get("score_sort_ms") or 0.0)
            momentum = float(performance.get("momentum_ms") or 0.0)
            performance["other_ms"] = round(
                max(0.0, total_ms - aggregate - score_sort - momentum - dual_rank_ms),
                3,
            )
        payload["calculate_ms"] = round(total_ms, 3)
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["default_theme_view"] = "momentum"
            policy["dual_server_rankings"] = True
            policy["browser_sort_allowed"] = False
            policy["current_money_ranking_preserved_until_stage3"] = False
            policy["fund_flow_bars"] = "server_relative_1m_5m"
        return payload

    builder_class.__call__ = call
    builder_class._stockboard_dual_rank_installed = True
