"""StockBoard ranking engine.

This module owns score, grade, and pool placement logic for candidate models.
The first dedicated model is NET_BUY_STRENGTH_V02.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


NET_BUY_STRENGTH_V02 = "NET_BUY_STRENGTH_V02"
NET_BUY_STRENGTH_TOTAL_POINTS = 700
NET_BUY_STRENGTH_FALLBACK_AMOUNT_SCORE = 60


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return float(number)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _round_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return round(_clamp(float(value), 0.0, 100.0), 2)


def _score_text(value: float | int | None) -> str:
    if value is None:
        return "-"
    number = float(value)
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def grade_for_percent(score: int | float | None) -> tuple[str | None, str]:
    """Return grade letter and css class. 60 or higher is D, below 60 is F."""
    if score is None:
        return None, ""
    score = int(round(_clamp(float(score), 0, 100)))
    if score >= 90:
        return "A", "a"
    if score >= 80:
        return "B", "b"
    if score >= 70:
        return "C", "c"
    if score >= 60:
        return "D", "d"
    return "F", "f"


def grade_text_for_percent(score: int | float | None) -> str:
    grade, _grade_class = grade_for_percent(score)
    if grade is None or score is None:
        return "-"
    return f"{grade}{int(round(_clamp(float(score), 0, 100)))}"


def _current_rank(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "rank", "displayed_rank", "current_rank"))


def _previous_rank(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "prev_rank", "previous_rank", "pred_rank"))


def _trade_value(row: dict[str, Any]) -> float | None:
    return _number_or_none(
        _first(
            row,
            "trade_value_eok",
            "realtime_acc_trade_value_eok_candidate",
            "realtimeAccTradeValueEokCandidate",
        )
    )


def _previous_trade_value(row: dict[str, Any]) -> float | None:
    return _number_or_none(
        _first(
            row,
            "prev_trade_value_eok",
            "previous_trade_value_eok",
            "yesterday_trade_value_eok",
        )
    )


def _bid_volume(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "bid_volume", "bid_volume_snapshot", "bidVolume"))


def _ask_volume(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "ask_volume", "ask_volume_snapshot", "askVolume"))


def _realtime_strength(row: dict[str, Any]) -> float | None:
    return _number_or_none(
        _first(
            row,
            "realtime_strength",
            "execution_strength",
            "realtime_strength_snapshot",
            "realtimeStrengthSnapshot",
        )
    )


def _one_min_strength_growth(row: dict[str, Any]) -> float | None:
    return _number_or_none(
        _first(row, "one_min_strength_growth_rate", "one_min_strength_delta")
    )


def _program_net(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "program_net", "program_sum", "program_net_eok"))


def _rank_position_score(position: int | None, total_count: int) -> float:
    if position is None or total_count <= 0:
        return 0.0
    if total_count == 1:
        return 100.0
    return _round_score(100 * (total_count - position) / (total_count - 1))


def _rank_value_scores(
    rows: list[dict[str, Any]],
    values: dict[int, float],
    *,
    descending: bool = True,
) -> dict[int, float]:
    """Score rows by value rank using the full filtered row count as denominator."""
    total_count = len(rows)
    if total_count <= 0:
        return {}
    ordered = sorted(
        values.items(),
        key=lambda item: (item[1], -item[0]),
        reverse=descending,
    )
    result: dict[int, float] = {}
    last_value: float | None = None
    last_position: int | None = None
    for ordinal, (row_index, value) in enumerate(ordered, start=1):
        if last_value is not None and value == last_value and last_position is not None:
            position = last_position
        else:
            position = ordinal
            last_value = value
            last_position = position
        result[row_index] = _rank_position_score(position, total_count)
    return result


def _pool_stage(funnel_rank: int | None) -> str:
    if funnel_rank is None:
        return "top300"
    if funnel_rank <= 5:
        return "top5"
    if funnel_rank <= 20:
        return "top20"
    if funnel_rank <= 50:
        return "top50"
    return "top300"


@dataclass(frozen=True)
class ScoreItem:
    key: str
    label: str
    points: float
    source: str
    status: str = "ok"
    value: float | None = None
    raw_value: float | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "points": _round_score(self.points),
            "possible_points": 100,
            "source": self.source,
            "status": self.status,
            "coverage_status": self.status,
            "value": self.value,
            "raw_value": self.raw_value,
            "reason": self.reason,
        }


class NetBuyStrengthV02RankingEngine:
    """Compute NET_BUY_STRENGTH_V02 score, grade, and pool placement."""

    model_id = NET_BUY_STRENGTH_V02
    model_name = "순매수 강도 v0.2"

    def __init__(self, model: dict[str, Any] | None = None):
        self.model = model or {}
        self.model_id = str(self.model.get("id") or self.model_id)
        self.model_name = str(self.model.get("name") or self.model_name)

    def enrich(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched_rows = [dict(row) for row in rows]
        total_count = len(enriched_rows)
        amount_ratios: dict[int, float] = {}
        one_min_growth_values: dict[int, float] = {}
        program_ratios: dict[int, float] = {}

        for index, row in enumerate(enriched_rows):
            trade_value = _trade_value(row)
            prev_trade_value = _previous_trade_value(row)
            if trade_value is not None and trade_value > 0 and prev_trade_value is not None and prev_trade_value > 0:
                amount_ratios[index] = trade_value / prev_trade_value

            one_min_growth = _one_min_strength_growth(row)
            if one_min_growth is not None and one_min_growth > 0:
                one_min_growth_values[index] = one_min_growth

            program_net = _program_net(row)
            if trade_value is not None and trade_value > 0 and program_net is not None and program_net > 0:
                program_ratios[index] = program_net / trade_value

        amount_scores = _rank_value_scores(enriched_rows, amount_ratios)
        one_min_scores = _rank_value_scores(enriched_rows, one_min_growth_values)
        program_scores = _rank_value_scores(enriched_rows, program_ratios)

        for index, row in enumerate(enriched_rows):
            score_items = self._score_items(
                row,
                index=index,
                total_count=total_count,
                amount_ratios=amount_ratios,
                amount_scores=amount_scores,
                one_min_growth_values=one_min_growth_values,
                one_min_scores=one_min_scores,
                program_ratios=program_ratios,
                program_scores=program_scores,
            )
            self._apply_score(row, score_items)

        ranked_rows = sorted(
            enriched_rows,
            key=lambda row: (
                -(_number_or_none(row.get("score_total_points")) or -1),
                _current_rank(row) or float("inf"),
                -(_trade_value(row) or 0),
            ),
        )
        for funnel_rank, row in enumerate(ranked_rows, start=1):
            row["funnel_rank"] = funnel_rank
            row["pool_rank"] = funnel_rank
            row["pool_stage"] = _pool_stage(funnel_rank)
            row["is_candidate"] = funnel_rank <= 5
            row["candidate_rank"] = funnel_rank if funnel_rank <= 5 else None

        return enriched_rows

    def _score_items(
        self,
        row: dict[str, Any],
        *,
        index: int,
        total_count: int,
        amount_ratios: dict[int, float],
        amount_scores: dict[int, float],
        one_min_growth_values: dict[int, float],
        one_min_scores: dict[int, float],
        program_ratios: dict[int, float],
        program_scores: dict[int, float],
    ) -> list[ScoreItem]:
        return [
            self._rank_score(row, total_count),
            self._previous_rank_score(row),
            self._amount_score(row, index, amount_ratios, amount_scores),
            self._ask_share_score(row),
            self._instant_strength_score(row),
            self._one_min_strength_score(index, one_min_growth_values, one_min_scores),
            self._program_score(row, index, program_ratios, program_scores),
        ]

    def _rank_score(self, row: dict[str, Any], total_count: int) -> ScoreItem:
        rank = _current_rank(row)
        if rank is None:
            return ScoreItem("rank", "순위", 0, "rank", "missing", reason="current_rank_missing")
        return ScoreItem(
            "rank",
            "순위",
            _rank_position_score(int(rank), total_count),
            "rank",
            value=rank,
            reason="filtered_pool_rank_100_to_0",
        )

    def _previous_rank_score(self, row: dict[str, Any]) -> ScoreItem:
        rank = _current_rank(row)
        prev_rank = _previous_rank(row)
        if rank is None or prev_rank is None:
            return ScoreItem(
                "previous_rank",
                "전일",
                0,
                "prev_rank-rank",
                "missing",
                reason="rank_or_prev_rank_missing",
            )
        gap = prev_rank - rank
        points = _round_score(min(max(gap, 0), 100))
        status = "capped" if gap > 100 else "ok"
        return ScoreItem(
            "previous_rank",
            "전일",
            points,
            "prev_rank-rank",
            status,
            value=gap,
            reason="rank_gap_capped_at_100",
        )

    def _amount_score(
        self,
        row: dict[str, Any],
        index: int,
        amount_ratios: dict[int, float],
        amount_scores: dict[int, float],
    ) -> ScoreItem:
        prev_trade_value = _previous_trade_value(row)
        if index not in amount_ratios:
            fallback_status = str(row.get("prev_trade_value_status") or "fallback")
            return ScoreItem(
                "trade_value_ratio",
                "금액(억)",
                NET_BUY_STRENGTH_FALLBACK_AMOUNT_SCORE,
                "trade_value_eok/prev_trade_value_eok",
                fallback_status,
                reason="전일대금 미확인 60점",
            )
        return ScoreItem(
            "trade_value_ratio",
            "금액(억)",
            amount_scores.get(index, 0),
            "trade_value_eok/prev_trade_value_eok",
            "ok",
            value=amount_ratios[index],
            raw_value=prev_trade_value,
            reason="ranked_trade_value_ratio",
        )

    def _ask_share_score(self, row: dict[str, Any]) -> ScoreItem:
        bid = _bid_volume(row)
        ask = _ask_volume(row)
        if bid is None or ask is None or bid + ask <= 0:
            return ScoreItem(
                "ask_share",
                "잔량비",
                0,
                "ask/(bid+ask)",
                "missing",
                reason="orderbook_volume_missing",
            )
        ask_share = ask / (bid + ask) * 100
        return ScoreItem(
            "ask_share",
            "잔량비",
            ask_share,
            "ask/(bid+ask)",
            value=ask_share,
            reason="sell_orderbook_share",
        )

    def _instant_strength_score(self, row: dict[str, Any]) -> ScoreItem:
        strength = _realtime_strength(row)
        if strength is None:
            return ScoreItem(
                "instant_strength",
                "순간강도",
                0,
                "realtime_strength",
                "missing",
                reason="realtime_strength_missing",
            )
        return ScoreItem(
            "instant_strength",
            "순간강도",
            _clamp(strength, 0, 200) / 200 * 100,
            "realtime_strength",
            value=strength,
            reason="0_to_200_strength_linear_score",
        )

    def _one_min_strength_score(
        self,
        index: int,
        one_min_growth_values: dict[int, float],
        one_min_scores: dict[int, float],
    ) -> ScoreItem:
        if index not in one_min_growth_values:
            return ScoreItem(
                "one_min_strength",
                "1분강도",
                0,
                "one_min_strength_growth_rate|one_min_strength_delta",
                "missing",
                reason="one_min_strength_growth_missing_or_nonpositive",
            )
        return ScoreItem(
            "one_min_strength",
            "1분강도",
            one_min_scores.get(index, 0),
            "one_min_strength_growth_rate|one_min_strength_delta",
            "ok",
            value=one_min_growth_values[index],
            reason="ranked_one_min_strength_growth",
        )

    def _program_score(
        self,
        row: dict[str, Any],
        index: int,
        program_ratios: dict[int, float],
        program_scores: dict[int, float],
    ) -> ScoreItem:
        program_net = _program_net(row)
        if program_net is None:
            return ScoreItem(
                "program_ratio",
                "프로(억)",
                0,
                "program_net/trade_value_eok",
                "missing",
                reason="program_net_missing",
            )
        if program_net <= 0:
            return ScoreItem(
                "program_ratio",
                "프로(억)",
                0,
                "program_net/trade_value_eok",
                "nonpositive",
                value=program_net,
                reason="program_net_nonpositive",
            )
        if index not in program_ratios:
            return ScoreItem(
                "program_ratio",
                "프로(억)",
                0,
                "program_net/trade_value_eok",
                "missing",
                value=program_net,
                reason="trade_value_missing_for_program_ratio",
            )
        return ScoreItem(
            "program_ratio",
            "프로(억)",
            program_scores.get(index, 0),
            "program_net/trade_value_eok",
            "ok",
            value=program_ratios[index],
            raw_value=program_net,
            reason="ranked_program_ratio",
        )

    def _apply_score(self, row: dict[str, Any], score_items: list[ScoreItem]) -> None:
        item_dicts = [item.as_dict() for item in score_items]
        score_total_points = round(sum(item["points"] for item in item_dicts), 2)
        score_percent = int(round(_clamp(score_total_points / NET_BUY_STRENGTH_TOTAL_POINTS * 100, 0, 100)))
        grade, grade_class = grade_for_percent(score_percent)
        grade_text = grade_text_for_percent(score_percent)
        score_status = "fallback" if any(item["status"] == "fallback" for item in item_dicts) else (
            "partial" if any(item["status"] == "missing" for item in item_dicts) else "ok"
        )

        row.update(
            {
                "candidate_model_id": self.model_id,
                "candidate_model_name": self.model_name,
                "candidate_score_version": self.model_id,
                "candidate_score_raw": score_total_points,
                "candidate_score_max": NET_BUY_STRENGTH_TOTAL_POINTS,
                "candidate_score": score_percent,
                "score_total": score_total_points,
                "score_total_points": score_total_points,
                "score_possible_points": NET_BUY_STRENGTH_TOTAL_POINTS,
                "score_percent": score_percent,
                "grade_score": score_percent,
                "grade_letter": grade,
                "legacy_grade": _first(row, "grade", "legacy_grade"),
                "candidate_grade": grade,
                "candidate_grade_text": grade_text,
                "candidate_grade_class": grade_class,
                "display_grade_source": "candidate_grade_text",
                "grade_fallback_used": False,
                "score_top50": score_percent,
                "score_top20": score_percent,
                "score_top5": score_percent,
                "entry_score": score_percent,
                "confirmation_score": score_percent,
                "focus_score": score_percent,
                "score_status": score_status,
                "candidate_status": "READY" if score_percent >= 60 else "WEAK",
                "candidate_score_coverage": round(
                    len([item for item in item_dicts if item["status"] not in {"missing"}])
                    / len(item_dicts),
                    2,
                ),
                "candidate_score_items": {
                    "net_buy_strength": {item["key"]: item["points"] for item in item_dicts},
                    "entry_score": {item["key"]: item["points"] for item in item_dicts},
                    "confirmation_score": {item["key"]: item["points"] for item in item_dicts},
                    "focus_score": {item["key"]: item["points"] for item in item_dicts},
                },
                "score_breakdown": {
                    "net_buy_strength": {
                        "score": score_total_points,
                        "possible_points": NET_BUY_STRENGTH_TOTAL_POINTS,
                        "percent": score_percent,
                        "items": item_dicts,
                    },
                    "total": {
                        "score": score_total_points,
                        "possible_points": NET_BUY_STRENGTH_TOTAL_POINTS,
                        "percent": score_percent,
                        "grade": grade_text,
                        "formula": "sum seven 100-point components",
                    },
                    "component_status": {item["key"]: item["status"] for item in item_dicts},
                },
                "score_sources": {"ranking_engine": self.model_id},
                "momentum": self._momentum_text(item_dicts),
                "candidate_reason": self._reason_text(item_dicts),
                "candidate_reason_tokens": [item["label"] for item in item_dicts if item["points"] >= 60],
            }
        )

    def _momentum_text(self, item_dicts: list[dict[str, Any]]) -> str:
        strong_labels = [item["label"] for item in item_dicts if item["points"] >= 70]
        return " + ".join(strong_labels[:4]) if strong_labels else "순매수강도 약함"

    def _reason_text(self, item_dicts: list[dict[str, Any]]) -> str:
        return " + ".join(
            f"{item['label']} {_score_text(item['points'])}점" for item in item_dicts
        )


def enrich_net_buy_strength_v02_fields(
    rows: Iterable[dict[str, Any]],
    model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return NetBuyStrengthV02RankingEngine(model).enrich(rows)
