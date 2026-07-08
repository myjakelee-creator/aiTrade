"""StockBoard ranking engine.

This module owns score, grade, and pool placement logic for candidate models.
The first dedicated model is NET_BUY_STRENGTH_V02.
"""

from __future__ import annotations

import json

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


NET_BUY_STRENGTH_V02 = "NET_BUY_STRENGTH_V02"
NET_BUY_STRENGTH_REGULAR_TOTAL_POINTS = 700
NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS = 600
NET_BUY_STRENGTH_TOTAL_POINTS = NET_BUY_STRENGTH_REGULAR_TOTAL_POINTS
NET_BUY_STRENGTH_FALLBACK_AMOUNT_SCORE = 60
NET_BUY_STRENGTH_MISSING_STRENGTH_SCORE = 50


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


def _five_min_strength(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "strength_5m", "five_min_strength"))


def _program_net(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "program_net", "program_sum", "program_net_eok"))


def _after_close_context(row: dict[str, Any], *, five_min_strength: float | None = None) -> bool:
    """Return True when the one-minute row should use after-close denominator logic."""
    session = str(
        _first(
            row,
            "market_session",
            "stockboard_market_session",
            "market_clock_phase",
            "market_phase",
            "session",
        )
        or ""
    ).strip().lower()
    if session in {"장마감", "애프터마켓", "aftermarket", "after_close", "closed", "close"}:
        return True
    if "장마감" in session or "애프터" in session or "after" in session or "close" in session:
        return True
    # Current runtime attaches strength_5m only when the real-time one-minute bucket is unavailable.
    # Until every row carries an explicit market session, this is the safest after-close inference.
    return five_min_strength is not None


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
    possible_points: float = 100

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "points": _round_score(self.points),
            "possible_points": _round_score(self.possible_points),
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
            self._one_min_strength_score(row, index, one_min_growth_values, one_min_scores),
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
        row: dict[str, Any],
        index: int,
        one_min_growth_values: dict[int, float],
        one_min_scores: dict[int, float],
    ) -> ScoreItem:
        if index in one_min_growth_values:
            return ScoreItem(
                "one_min_strength",
                "1분강도",
                one_min_scores.get(index, 0),
                "one_min_strength_growth_rate|one_min_strength_delta",
                "ok",
                value=one_min_growth_values[index],
                reason="ranked_one_min_strength_growth",
            )

        five_min_strength = _five_min_strength(row)
        if _after_close_context(row, five_min_strength=five_min_strength):
            return ScoreItem(
                "one_min_strength",
                "1분강도",
                0,
                "strength_5m_after_close",
                "display_only" if five_min_strength is not None else "display_missing",
                value=five_min_strength,
                reason="after_close_5m_strength_display_only_denominator_600",
                possible_points=0,
            )

        return ScoreItem(
            "one_min_strength",
            "1분강도",
            NET_BUY_STRENGTH_MISSING_STRENGTH_SCORE,
            "one_min_strength_or_strength_5m_missing",
            "fallback",
            reason="regular_one_min_strength_missing_neutral_50",
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
        score_possible_points = round(sum(item["possible_points"] for item in item_dicts), 2)
        if score_possible_points <= 0:
            score_possible_points = NET_BUY_STRENGTH_REGULAR_TOTAL_POINTS
        score_percent = int(round(_clamp(score_total_points / score_possible_points * 100, 0, 100)))
        grade, grade_class = grade_for_percent(score_percent)
        grade_text = grade_text_for_percent(score_percent)
        score_status = "fallback" if any(item["status"] == "fallback" for item in item_dicts) else (
            "partial" if any(item["status"] == "missing" for item in item_dicts) else "ok"
        )
        scorable_items = [item for item in item_dicts if item["possible_points"] > 0]

        row.update(
            {
                "candidate_model_id": self.model_id,
                "candidate_model_name": self.model_name,
                "candidate_score_version": self.model_id,
                "candidate_score_raw": score_total_points,
                "candidate_score_max": score_possible_points,
                "candidate_score": score_percent,
                "score_total": score_total_points,
                "score_total_points": score_total_points,
                "score_possible_points": score_possible_points,
                "score_percent": score_percent,
                "score_denominator_points": score_possible_points,
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
                    len([item for item in scorable_items if item["status"] not in {"missing"}])
                    / len(scorable_items),
                    2,
                ) if scorable_items else None,
                "candidate_score_items": {
                    "net_buy_strength": {item["key"]: item["points"] for item in item_dicts},
                    "entry_score": {item["key"]: item["points"] for item in item_dicts},
                    "confirmation_score": {item["key"]: item["points"] for item in item_dicts},
                    "focus_score": {item["key"]: item["points"] for item in item_dicts},
                },
                "score_breakdown": {
                    "net_buy_strength": {
                        "score": score_total_points,
                        "possible_points": score_possible_points,
                        "percent": score_percent,
                        "items": item_dicts,
                    },
                    "total": {
                        "score": score_total_points,
                        "possible_points": score_possible_points,
                        "percent": score_percent,
                        "grade": grade_text,
                        "formula": "round(score_total_points / score_possible_points * 100)",
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


# ---------------------------------------------------------------------------
# Config-driven candidate model ranking
# ---------------------------------------------------------------------------

CANDIDATE_MODEL_DIR = Path(__file__).resolve().parent / "configs" / "candidate_models"
_CANDIDATE_MODEL_REGISTRY_CACHE: dict[str, Any] | None = None
_CANDIDATE_MODEL_CONFIG_CACHE: dict[str, dict[str, Any]] = {}


def _read_candidate_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_candidate_model_registry(*, include_configs: bool = False) -> dict[str, Any]:
    global _CANDIDATE_MODEL_REGISTRY_CACHE

    if _CANDIDATE_MODEL_REGISTRY_CACHE is None:
        path = CANDIDATE_MODEL_DIR / "_registry.json"
        payload = _read_candidate_json(path) or {"models": []}
        payload.setdefault("schema_version", 1)
        payload.setdefault("source", str(path))
        _CANDIDATE_MODEL_REGISTRY_CACHE = payload

    result = dict(_CANDIDATE_MODEL_REGISTRY_CACHE)

    if include_configs:
        configs: dict[str, Any] = {}
        for item in result.get("models") or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            if not model_id:
                continue
            config = load_candidate_model_config(model_id)
            if config:
                configs[model_id] = config
        result["configs"] = configs

    return result


def load_candidate_model_config(model_id: str | None = None) -> dict[str, Any]:
    registry = load_candidate_model_registry()
    default_model_id = str(registry.get("default_model_id") or NET_BUY_STRENGTH_V02)
    requested_id = str(model_id or default_model_id)

    models = registry.get("models") if isinstance(registry.get("models"), list) else []
    selected = None
    for item in models:
        if isinstance(item, dict) and str(item.get("id") or "") == requested_id:
            selected = item
            break

    if selected is None and requested_id != default_model_id:
        return load_candidate_model_config(default_model_id)

    if not isinstance(selected, dict):
        selected = {"id": NET_BUY_STRENGTH_V02, "label": "??? ?? v0.2", "file": "NET_BUY_STRENGTH_V02.json"}

    selected_id = str(selected.get("id") or requested_id)
    if selected_id in _CANDIDATE_MODEL_CONFIG_CACHE:
        return dict(_CANDIDATE_MODEL_CONFIG_CACHE[selected_id])

    file_name = str(selected.get("file") or "")
    config = _read_candidate_json(CANDIDATE_MODEL_DIR / file_name) if file_name else None
    if not isinstance(config, dict):
        config = {}

    config.setdefault("id", selected_id)
    config.setdefault("label", selected.get("label") or config.get("name") or selected_id)
    config.setdefault("name", config.get("label") or selected.get("label") or selected_id)
    config.setdefault("source_file", str(CANDIDATE_MODEL_DIR / file_name) if file_name else "")

    _CANDIDATE_MODEL_CONFIG_CACHE[selected_id] = dict(config)
    return config


def _model_number(row: dict[str, Any], *keys: str) -> float | None:
    return _number_or_none(_first(row, *keys))


def _source_rank(row: dict[str, Any], fallback: int) -> float:
    return _number_or_none(row.get("_source_rank")) or _current_rank(row) or float(fallback)


def _row_ohlc_value(row: dict[str, Any], key: str) -> float | None:
    ohlc = row.get("ohlc")
    if isinstance(ohlc, dict):
        value = _number_or_none(ohlc.get(key))
        if value is not None:
            return value
    return _number_or_none(row.get(f"day_{key}") or row.get(key))


def _price_above_reference(row: dict[str, Any], *, allow_vwap: bool = False) -> float:
    price = _number_or_none(row.get("price") or row.get("trade_price"))
    if price is None:
        return 0.0

    ref = None
    if allow_vwap:
        ref = _number_or_none(row.get("vwap") or row.get("day_vwap"))

    if ref is None:
        ref = _row_ohlc_value(row, "open")

    if ref is None or ref <= 0:
        return 0.0

    return 100.0 if price >= ref else 0.0


def _safe_change_rate_score(row: dict[str, Any]) -> float:
    rate = _number_or_none(row.get("change_rate"))
    if rate is None or rate < 0:
        return 0.0
    if rate <= 12:
        return 100.0
    if rate <= 20:
        return 70.0
    if rate <= 30:
        return 30.0
    return 0.0


def _strength_score(value: Any) -> float:
    strength = _number_or_none(value)
    if strength is None or strength <= 0:
        return 0.0
    if strength >= 200:
        return 100.0
    if strength >= 100:
        return _round_score(60 + (strength - 100) / 100 * 40)
    return _round_score(strength / 100 * 50)


def _sell_wall_score(row: dict[str, Any]) -> float:
    ratio = _number_or_none(row.get("bid_ask_ratio"))
    if ratio is not None and ratio > 0:
        if ratio <= 0.2:
            return 100.0
        if ratio <= 0.4:
            return 90.0
        if ratio <= 0.6:
            return 80.0
        if ratio <= 0.8:
            return 70.0
        if ratio <= 1.0:
            return 60.0
        if ratio <= 1.2:
            return 30.0
        return 0.0

    bid = _bid_volume(row)
    ask = _ask_volume(row)
    if bid is None or ask is None or bid + ask <= 0:
        return 0.0
    return _round_score(ask / (bid + ask) * 100)


def _spike_reversal_penalty_score(row: dict[str, Any]) -> float:
    rate = _number_or_none(row.get("change_rate"))
    strength = _realtime_strength(row)
    price = _number_or_none(row.get("price") or row.get("trade_price"))
    open_price = _row_ohlc_value(row, "open")

    if rate is not None and rate > 20 and (strength is None or strength < 100):
        return 100.0
    if rate is not None and rate > 12 and price is not None and open_price is not None and price < open_price:
        return 100.0
    return 0.0


def _config_rank_scores(rows: list[dict[str, Any]], values: dict[int, float], *, descending: bool = True) -> dict[int, float]:
    return _rank_value_scores(rows, values, descending=descending)


def _config_context(rows: list[dict[str, Any]]) -> dict[str, dict[int, float]]:
    amount_values: dict[int, float] = {}
    program_values: dict[int, float] = {}
    net_buy_values: dict[int, float] = {}
    one_min_values: dict[int, float] = {}

    for index, row in enumerate(rows):
        trade_value = _trade_value(row)
        prev_trade_value = _previous_trade_value(row)
        if trade_value is not None and trade_value > 0 and prev_trade_value is not None and prev_trade_value > 0:
            amount_values[index] = trade_value / prev_trade_value

        program_net = _program_net(row)
        if program_net is not None and program_net > 0:
            if trade_value is not None and trade_value > 0:
                program_values[index] = program_net / trade_value
            else:
                program_values[index] = program_net

        large_net = _number_or_none(row.get("large_trade_net_count")) or 0.0
        if program_net is not None and program_net > 0 or large_net > 0:
            net_buy_values[index] = (program_net or 0.0) + large_net * 0.1

        one_min_growth = _one_min_strength_growth(row)
        one_min_strength = _number_or_none(row.get("strength_1m") or row.get("one_min_strength"))
        if one_min_growth is not None and one_min_growth > 0:
            one_min_values[index] = one_min_growth
        elif one_min_strength is not None and one_min_strength > 0:
            one_min_values[index] = one_min_strength

    return {
        "amount_scores": _config_rank_scores(rows, amount_values),
        "program_scores": _config_rank_scores(rows, program_values),
        "net_buy_scores": _config_rank_scores(rows, net_buy_values),
        "one_min_scores": _config_rank_scores(rows, one_min_values),
    }


def _config_key_score(
    row: dict[str, Any],
    *,
    index: int,
    total_count: int,
    context: dict[str, dict[int, float]],
    key: str,
) -> tuple[float, float | None, str]:
    source_rank = _source_rank(row, index + 1)

    if key == "trade_value_rank":
        return _rank_position_score(int(source_rank), total_count), source_rank, "trade_value_rank"

    if key in {"rank_gap", "rank_gap_continuation"}:
        prev_rank = _previous_rank(row)
        if prev_rank is None:
            return 0.0, None, "prev_rank_missing"
        gap = prev_rank - source_rank
        return _round_score(min(max(gap, 0), 100)), gap, "prev_rank-source_rank"

    if key in {"trade_value_growth", "one_min_trade_value_growth"}:
        return context["amount_scores"].get(index, 0.0), None, "rank(trade_value/prev_trade_value)"

    if key == "program_net":
        return context["program_scores"].get(index, 0.0), _program_net(row), "rank(program_net/trade_value)"

    if key in {"one_min_net_buy_value_growth", "one_min_net_buy_value_continuation"}:
        return context["net_buy_scores"].get(index, 0.0), None, "rank(program_net+large_trade_net)"

    if key in {"realtime_strength", "realtime_strength_hold_100"}:
        value = _realtime_strength(row)
        return _strength_score(value), value, "execution_strength"

    if key == "one_min_strength_growth":
        value = _number_or_none(row.get("strength_1m") or row.get("one_min_strength"))
        ranked = context["one_min_scores"].get(index)
        return (ranked if ranked is not None else _strength_score(value)), value, "strength_1m"

    if key == "sell_wall_absorption":
        value = _number_or_none(row.get("bid_ask_ratio"))
        return _sell_wall_score(row), value, "bid_ask_ratio"

    if key == "above_open":
        return _price_above_reference(row, allow_vwap=False), None, "price>=open"

    if key == "above_vwap_or_open":
        return _price_above_reference(row, allow_vwap=True), None, "price>=vwap_or_open"

    if key == "safe_change_rate_band":
        value = _number_or_none(row.get("change_rate"))
        return _safe_change_rate_score(row), value, "safe_change_rate_band"

    if key == "spike_reversal_penalty":
        value = _spike_reversal_penalty_score(row)
        return value, value, "spike_reversal_penalty"

    return 0.0, None, "unknown_model_key"


def _config_group_score(
    row: dict[str, Any],
    *,
    index: int,
    total_count: int,
    context: dict[str, dict[int, float]],
    items: list[dict[str, Any]],
) -> tuple[float, list[dict[str, Any]]]:
    if not items:
        return 0.0, []

    weighted = 0.0
    positive_weight_total = 0.0
    item_results: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "")
        label = str(item.get("label") or key)
        weight = _number_or_none(item.get("weight"))
        if weight is None or weight == 0:
            continue

        score, value, source = _config_key_score(
            row,
            index=index,
            total_count=total_count,
            context=context,
            key=key,
        )

        weighted += score * weight
        if weight > 0:
            positive_weight_total += weight

        item_results.append(
            {
                "key": key,
                "label": label,
                "points": _round_score(score),
                "weight": weight,
                "weighted_points": round(score * weight, 4),
                "possible_points": max(weight, 0),
                "source": source,
                "status": "ok" if source != "unknown_model_key" else "unknown",
                "value": value,
            }
        )

    if positive_weight_total <= 0:
        return 0.0, item_results

    return _round_score(weighted / positive_weight_total), item_results


def _config_grade_text(score: float, config: dict[str, Any]) -> str:
    bands = ((config.get("grade_policy") or {}).get("base_bands") or {})
    a = _number_or_none(bands.get("A")) or 90
    b = _number_or_none(bands.get("B")) or 80
    c = _number_or_none(bands.get("C")) or 70
    d = _number_or_none(bands.get("D")) or 60
    number = int(round(_clamp(score, 0, 100)))
    letter = "A" if number >= a else "B" if number >= b else "C" if number >= c else "D" if number >= d else "F"
    return f"{letter}{number}"


class ConfigDrivenCandidateRankingEngine:
    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.model_id = str(self.config.get("id") or NET_BUY_STRENGTH_V02)
        self.model_name = str(self.config.get("label") or self.config.get("name") or self.model_id)

    def enrich(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        score_structure = self.config.get("score_structure")
        if not isinstance(score_structure, dict):
            result = NetBuyStrengthV02RankingEngine(self.config).enrich(rows)
            return self._rerank_by_score(result)

        enriched = [dict(row) for row in rows]
        for index, row in enumerate(enriched):
            row["_source_rank"] = _current_rank(row) or index + 1
            row.setdefault("trade_value_rank", row["_source_rank"])

        total_count = len(enriched)
        context = _config_context(enriched)
        grade_weights = score_structure.get("grade_score_weights") if isinstance(score_structure.get("grade_score_weights"), dict) else {}
        entry_weight = _number_or_none(grade_weights.get("entry_score")) or 0.3333333333
        confirmation_weight = _number_or_none(grade_weights.get("confirmation_score")) or 0.3333333333
        focus_weight = _number_or_none(grade_weights.get("focus_score")) or 0.3333333334

        for index, row in enumerate(enriched):
            entry_score, entry_items = _config_group_score(
                row,
                index=index,
                total_count=total_count,
                context=context,
                items=score_structure.get("entry_score") or [],
            )
            confirmation_score, confirmation_items = _config_group_score(
                row,
                index=index,
                total_count=total_count,
                context=context,
                items=score_structure.get("confirmation_score") or [],
            )
            focus_score, focus_items = _config_group_score(
                row,
                index=index,
                total_count=total_count,
                context=context,
                items=score_structure.get("focus_score") or [],
            )

            score = _round_score(
                entry_score * entry_weight
                + confirmation_score * confirmation_weight
                + focus_score * focus_weight
            )
            grade_text = _config_grade_text(score, self.config)
            grade, grade_class = grade_for_percent(score)

            item_dicts = entry_items + confirmation_items + focus_items

            row.update(
                {
                    "candidate_model_id": self.model_id,
                    "candidate_model_name": self.model_name,
                    "candidate_score_version": self.model_id,
                    "entry_score": entry_score,
                    "confirmation_score": confirmation_score,
                    "focus_score": focus_score,
                    "candidate_score": score,
                    "score_percent": score,
                    "grade_score": score,
                    "candidate_grade": grade,
                    "candidate_grade_text": grade_text,
                    "candidate_grade_class": grade_class,
                    "display_grade_source": "candidate_model_config",
                    "score_total": score,
                    "score_total_points": score,
                    "score_possible_points": 100,
                    "score_breakdown": {
                        "candidate_model": {
                            "id": self.model_id,
                            "name": self.model_name,
                            "entry_score": entry_score,
                            "confirmation_score": confirmation_score,
                            "focus_score": focus_score,
                            "items": item_dicts,
                        },
                        "total": {
                            "score": score,
                            "possible_points": 100,
                            "percent": score,
                            "grade": grade_text,
                        },
                    },
                    "candidate_score_items": {
                        "entry_score": {item["key"]: item["points"] for item in entry_items},
                        "confirmation_score": {item["key"]: item["points"] for item in confirmation_items},
                        "focus_score": {item["key"]: item["points"] for item in focus_items},
                    },
                    "momentum": self._momentum_text(item_dicts),
                    "candidate_reason": self._reason_text(item_dicts),
                    "candidate_status": "READY" if score >= 60 else "WEAK",
                }
            )

        return self._rerank_by_score(enriched)

    def _rerank_by_score(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = [dict(row) for row in rows]
        for index, row in enumerate(enriched):
            row["_source_rank"] = _current_rank(row) or row.get("_source_rank") or index + 1
            row.setdefault("trade_value_rank", row["_source_rank"])
            row["candidate_model_id"] = self.model_id
            row["candidate_model_name"] = self.model_name

        ranked = sorted(
            enriched,
            key=lambda row: (
                -(_number_or_none(row.get("candidate_score") or row.get("score_percent") or row.get("grade_score")) or 0),
                _number_or_none(row.get("_source_rank")) or float("inf"),
                -(_trade_value(row) or 0),
                str(row.get("stock_code") or ""),
            ),
        )

        for rank, row in enumerate(ranked, start=1):
            previous_rank = _previous_rank(row)
            row["rank"] = rank
            row["model_rank"] = rank
            row["pool_rank"] = rank
            row["funnel_rank"] = rank
            row["rank_change"] = (previous_rank - rank) if previous_rank is not None else None
            row["pool_stage"] = _pool_stage(rank)
            row["is_candidate"] = rank <= 5
            row["candidate_rank"] = rank if rank <= 5 else None

        return ranked

    def _momentum_text(self, items: list[dict[str, Any]]) -> str:
        labels = [str(item.get("label") or item.get("key")) for item in items if _number_or_none(item.get("points")) is not None and float(item.get("points")) >= 70]
        return " + ".join(labels[:4]) if labels else "???? ??"

    def _reason_text(self, items: list[dict[str, Any]]) -> str:
        return " + ".join(f"{item.get('label') or item.get('key')} {_score_text(item.get('points'))}?" for item in items)


def enrich_candidate_model_fields(
    rows: Iterable[dict[str, Any]],
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    config = load_candidate_model_config(model_id)
    return ConfigDrivenCandidateRankingEngine(config).enrich(rows)

