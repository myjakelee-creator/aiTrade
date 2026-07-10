"""StockBoard server-side ranking engine.

All score, grade, model-rank, and pool metadata are calculated here.
The browser must only display fields supplied by the worker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

NET_BUY_STRENGTH_V02 = "NET_BUY_STRENGTH_V02"
FIVE_FACTOR_FLOW_V01 = "FIVE_FACTOR_FLOW_V01"

NET_BUY_STRENGTH_REGULAR_TOTAL_POINTS = 700
NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS = 700
NET_BUY_STRENGTH_TOTAL_POINTS = NET_BUY_STRENGTH_REGULAR_TOTAL_POINTS
NET_BUY_STRENGTH_FALLBACK_AMOUNT_SCORE = 60
NET_BUY_STRENGTH_MISSING_STRENGTH_SCORE = 0

CANDIDATE_MODEL_DIR = Path(__file__).resolve().parent / "configs" / "candidate_models"
_CANDIDATE_MODEL_REGISTRY_CACHE: dict[str, Any] | None = None
_CANDIDATE_MODEL_CONFIG_CACHE: dict[str, dict[str, Any]] = {}


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


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def _age_sec(value: Any) -> float | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return None
    return max(0.0, (datetime.now() - parsed).total_seconds())


def grade_for_percent(score: int | float | None) -> tuple[str | None, str]:
    """Map the calculated score directly to a grade."""
    if score is None:
        return None, ""
    number = int(round(_clamp(float(score), 0, 100)))
    if number >= 90:
        return "A", "a"
    if number >= 80:
        return "B", "b"
    if number >= 70:
        return "C", "c"
    if number >= 60:
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


def _five_min_strength(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "strength_5m", "five_min_strength"))


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


def _relative_bucket_scores(
    values: dict[int, float],
    *,
    bands: tuple[tuple[float, float], ...],
) -> dict[int, float]:
    """Score valid values by percentile position within the available set."""
    ordered = sorted(values.items(), key=lambda item: (item[1], -item[0]), reverse=True)
    count = len(ordered)
    if count == 0:
        return {}
    effective_count = max(count, 20)
    result: dict[int, float] = {}
    last_value: float | None = None
    last_position = 0
    for ordinal, (index, value) in enumerate(ordered, start=1):
        if last_value is None or value != last_value:
            last_position = ordinal
            last_value = value
        percentile = last_position / effective_count
        points = bands[-1][1]
        for maximum_percentile, band_points in bands:
            if percentile <= maximum_percentile:
                points = band_points
                break
        result[index] = float(points)
    return result


def _pool_stage(funnel_rank: int | None) -> str:
    """Top5 is not a separate lane in the current v2 layout."""
    if funnel_rank is None:
        return "top300"
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
    """Legacy v0.2 model with Kiwoom five-minute strength replacing one-minute strength."""

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
        program_ratios: dict[int, float] = {}

        for index, row in enumerate(enriched_rows):
            trade_value = _trade_value(row)
            previous_value = _previous_trade_value(row)
            if (
                trade_value is not None
                and trade_value > 0
                and previous_value is not None
                and previous_value > 0
            ):
                amount_ratios[index] = trade_value / previous_value

            program_net = _program_net(row)
            if trade_value is not None and trade_value > 0 and program_net is not None and program_net > 0:
                program_ratios[index] = program_net / trade_value

        amount_scores = _rank_value_scores(enriched_rows, amount_ratios)
        program_scores = _rank_value_scores(enriched_rows, program_ratios)

        for index, row in enumerate(enriched_rows):
            items = [
                self._rank_score(row, total_count),
                self._previous_rank_score(row),
                self._amount_score(row, index, amount_ratios, amount_scores),
                self._ask_share_score(row),
                self._instant_strength_score(row),
                self._five_min_strength_score(row),
                self._program_score(row, index, program_ratios, program_scores),
            ]
            self._apply_score(row, items)

        return self._rerank(enriched_rows)

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
        previous_rank = _previous_rank(row)
        if rank is None or previous_rank is None:
            return ScoreItem(
                "previous_rank",
                "전일",
                0,
                "prev_rank-rank",
                "missing",
                reason="rank_or_prev_rank_missing",
            )
        gap = previous_rank - rank
        return ScoreItem(
            "previous_rank",
            "전일",
            _round_score(min(max(gap, 0), 100)),
            "prev_rank-rank",
            "capped" if gap > 100 else "ok",
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
        if index not in amount_ratios:
            return ScoreItem(
                "trade_value_ratio",
                "금액(억)",
                NET_BUY_STRENGTH_FALLBACK_AMOUNT_SCORE,
                "trade_value_eok/prev_trade_value_eok",
                str(row.get("prev_trade_value_status") or "fallback"),
                reason="전일대금 미확인 60점",
            )
        return ScoreItem(
            "trade_value_ratio",
            "금액(억)",
            amount_scores.get(index, 0),
            "trade_value_eok/prev_trade_value_eok",
            value=amount_ratios[index],
            raw_value=_previous_trade_value(row),
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
                "execution_strength",
                "missing",
                reason="execution_strength_missing",
            )
        return ScoreItem(
            "instant_strength",
            "순간강도",
            _strength_score(strength),
            "execution_strength",
            value=strength,
            reason="execution_strength_absolute_score",
        )

    def _five_min_strength_score(self, row: dict[str, Any]) -> ScoreItem:
        strength = _five_min_strength(row)
        if strength is None:
            return ScoreItem(
                "five_min_strength",
                "5분강도",
                0,
                "strength_5m",
                "missing",
                reason="kiwoom_five_min_strength_not_queried_yet",
            )
        return ScoreItem(
            "five_min_strength",
            "5분강도",
            _strength_score(strength),
            "strength_5m",
            value=strength,
            reason="kiwoom_five_min_strength_absolute_score",
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
        return ScoreItem(
            "program_ratio",
            "프로(억)",
            program_scores.get(index, 0),
            "program_net/trade_value_eok",
            value=program_ratios.get(index),
            raw_value=program_net,
            reason="ranked_program_ratio",
        )

    def _apply_score(self, row: dict[str, Any], items: list[ScoreItem]) -> None:
        item_dicts = [item.as_dict() for item in items]
        total = round(sum(item["points"] for item in item_dicts), 2)
        possible = round(sum(item["possible_points"] for item in item_dicts), 2) or 700
        score = int(round(_clamp(total / possible * 100, 0, 100)))
        grade, grade_class = grade_for_percent(score)
        grade_text = grade_text_for_percent(score)
        status = (
            "fallback"
            if any(item["status"] == "fallback" for item in item_dicts)
            else "partial"
            if any(item["status"] == "missing" for item in item_dicts)
            else "ok"
        )
        covered = len([item for item in item_dicts if item["status"] != "missing"])
        row.update(
            {
                "candidate_model_id": self.model_id,
                "candidate_model_name": self.model_name,
                "candidate_score_version": self.model_id,
                "candidate_score_raw": total,
                "candidate_score_max": possible,
                "candidate_score": score,
                "score_total": total,
                "score_total_points": total,
                "score_possible_points": possible,
                "score_percent": score,
                "score_denominator_points": possible,
                "grade_score": score,
                "grade_letter": grade,
                "candidate_grade": grade,
                "candidate_grade_text": grade_text,
                "candidate_grade_class": grade_class,
                "display_grade_source": "candidate_grade_text",
                "entry_score": score,
                "confirmation_score": score,
                "focus_score": score,
                "score_top50": score,
                "score_top20": score,
                "score_top5": score,
                "score_status": status,
                "candidate_status": "READY" if score >= 60 else "WEAK",
                "candidate_score_coverage": round(covered / len(item_dicts), 2) if item_dicts else None,
                "score_updated_at": _now_text(),
                "candidate_score_items": {
                    "entry_score": {item["key"]: item["points"] for item in item_dicts},
                    "confirmation_score": {item["key"]: item["points"] for item in item_dicts},
                    "focus_score": {item["key"]: item["points"] for item in item_dicts},
                },
                "score_breakdown": {
                    "net_buy_strength": {
                        "score": total,
                        "possible_points": possible,
                        "percent": score,
                        "items": item_dicts,
                    },
                    "total": {
                        "score": total,
                        "possible_points": possible,
                        "percent": score,
                        "grade": grade_text,
                    },
                },
                "momentum": self._momentum_text(item_dicts),
                "candidate_reason": self._reason_text(item_dicts),
            }
        )

    def _rerank(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked = sorted(
            rows,
            key=lambda row: (
                -(_number_or_none(row.get("candidate_score")) or 0),
                _current_rank(row) or float("inf"),
                -(_trade_value(row) or 0),
                str(row.get("stock_code") or ""),
            ),
        )
        for rank, row in enumerate(ranked, start=1):
            row["model_rank"] = rank
            row["pool_rank"] = rank
            row["funnel_rank"] = rank
            row["pool_stage"] = _pool_stage(rank)
            row["is_candidate"] = rank <= 5
            row["candidate_rank"] = rank if rank <= 5 else None
        return ranked

    def _momentum_text(self, items: list[dict[str, Any]]) -> str:
        labels = [item["label"] for item in items if item["points"] >= 70]
        return " + ".join(labels[:4]) if labels else "순매수강도 약함"

    def _reason_text(self, items: list[dict[str, Any]]) -> str:
        return " + ".join(f"{item['label']} {_score_text(item['points'])}점" for item in items)


def enrich_net_buy_strength_v02_fields(
    rows: Iterable[dict[str, Any]],
    model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    return NetBuyStrengthV02RankingEngine(model).enrich(rows)


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
    default_model_id = str(registry.get("default_model_id") or FIVE_FACTOR_FLOW_V01)
    requested_id = str(model_id or default_model_id)

    models = registry.get("models") if isinstance(registry.get("models"), list) else []
    selected = next(
        (
            item
            for item in models
            if isinstance(item, dict) and str(item.get("id") or "") == requested_id
        ),
        None,
    )
    if selected is None and requested_id != default_model_id:
        return load_candidate_model_config(default_model_id)
    if not isinstance(selected, dict):
        selected = {
            "id": FIVE_FACTOR_FLOW_V01,
            "label": "5요소 수급선발 v0.1",
            "file": "FIVE_FACTOR_FLOW_V01.json",
        }

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
    reference = None
    if allow_vwap:
        reference = _number_or_none(row.get("vwap") or row.get("day_vwap"))
    if reference is None:
        reference = _row_ohlc_value(row, "open")
    if reference is None or reference <= 0:
        return 0.0
    return 100.0 if price >= reference else 0.0


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


def _config_context(rows: list[dict[str, Any]]) -> dict[str, dict[int, float]]:
    amount_values: dict[int, float] = {}
    program_values: dict[int, float] = {}
    net_buy_values: dict[int, float] = {}
    for index, row in enumerate(rows):
        trade_value = _trade_value(row)
        previous_value = _previous_trade_value(row)
        if (
            trade_value is not None
            and trade_value > 0
            and previous_value is not None
            and previous_value > 0
        ):
            amount_values[index] = trade_value / previous_value

        program_net = _program_net(row)
        if program_net is not None and program_net > 0:
            program_values[index] = (
                program_net / trade_value
                if trade_value is not None and trade_value > 0
                else program_net
            )

        large_net = _number_or_none(row.get("large_trade_net_sum_eok"))
        if large_net is None:
            large_net = (_number_or_none(row.get("large_trade_net_count")) or 0.0) * 0.1
        if (program_net is not None and program_net > 0) or large_net > 0:
            net_buy_values[index] = (program_net or 0.0) + large_net

    return {
        "amount_scores": _rank_value_scores(rows, amount_values),
        "program_scores": _rank_value_scores(rows, program_values),
        "net_buy_scores": _rank_value_scores(rows, net_buy_values),
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
        previous_rank = _previous_rank(row)
        if previous_rank is None:
            return 0.0, None, "prev_rank_missing"
        gap = previous_rank - source_rank
        return _round_score(min(max(gap, 0), 100)), gap, "prev_rank-source_rank"

    if key in {
        "trade_value_growth",
        "one_min_trade_value_growth",
        "one_min_trade_value_burst",
        "no_trade_value_collapse",
    }:
        return context["amount_scores"].get(index, 0.0), None, "rank(trade_value/prev_trade_value)"

    if key in {"program_net", "program_net_growth"}:
        return context["program_scores"].get(index, 0.0), _program_net(row), "rank(program_net/trade_value)"

    if key in {"one_min_net_buy_value_growth", "one_min_net_buy_value_continuation"}:
        return context["net_buy_scores"].get(index, 0.0), None, "rank(program_net+large_trade_net)"

    if key in {
        "realtime_strength",
        "realtime_strength_hold_100",
        "realtime_strength_hold",
        "realtime_strength_rank",
        "session_strength",
    }:
        value = _realtime_strength(row)
        return _strength_score(value), value, "execution_strength"

    if key in {
        "five_min_strength",
        "one_min_strength_growth",
        "one_min_strength_acceleration",
    }:
        value = _five_min_strength(row)
        source = "strength_5m" if value is not None else "strength_5m_missing"
        return _strength_score(value), value, source

    if key == "sell_wall_absorption":
        value = _number_or_none(row.get("bid_ask_ratio"))
        return _sell_wall_score(row), value, "bid_ask_ratio"

    if key in {"above_open", "breakout_or_above_open"}:
        return _price_above_reference(row, allow_vwap=False), None, "price>=open"

    if key in {"above_vwap", "above_vwap_hold", "above_vwap_or_open"}:
        return _price_above_reference(row, allow_vwap=True), None, "price>=vwap_or_open"

    if key in {
        "safe_change_rate_band",
        "change_rate_reaction",
        "change_rate_0_to_7",
        "not_overheated_volatility",
        "not_overextended",
    }:
        value = _number_or_none(row.get("change_rate"))
        if key == "change_rate_0_to_7":
            score = 100.0 if value is not None and 0 <= value <= 7 else 0.0
        elif key in {"not_overheated_volatility", "not_overextended"}:
            score = 100.0 if value is not None and -3 <= value <= 12 else 0.0
        else:
            score = _safe_change_rate_score(row)
        return score, value, key

    if key == "program_plus_strength":
        program_score = context["program_scores"].get(index, 0.0)
        strength_value = _realtime_strength(row)
        return _round_score((program_score + _strength_score(strength_value)) / 2), strength_value, "program_plus_execution_strength"

    if key in {"spike_reversal_penalty", "burst_reversal_penalty", "overheat_penalty"}:
        value = _spike_reversal_penalty_score(row)
        return value, value, key

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
        status = (
            "unknown"
            if source == "unknown_model_key"
            else "missing"
            if source.endswith("_missing")
            else "ok"
        )
        item_results.append(
            {
                "key": key,
                "label": label,
                "points": _round_score(score),
                "weight": weight,
                "weighted_points": round(score * weight, 4),
                "possible_points": max(weight, 0),
                "source": source,
                "status": status,
                "value": value,
            }
        )

    if positive_weight_total <= 0:
        return 0.0, item_results
    return _round_score(weighted / positive_weight_total), item_results


def _config_grade_text(score: float, _config: dict[str, Any]) -> str:
    return grade_text_for_percent(score)


class ConfigDrivenCandidateRankingEngine:
    def __init__(self, config: dict[str, Any]):
        self.config = config or {}
        self.model_id = str(self.config.get("id") or NET_BUY_STRENGTH_V02)
        self.model_name = str(self.config.get("label") or self.config.get("name") or self.model_id)

    def enrich(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        score_structure = self.config.get("score_structure")
        if not isinstance(score_structure, dict):
            return NetBuyStrengthV02RankingEngine(self.config).enrich(rows)

        enriched = [dict(row) for row in rows]
        for index, row in enumerate(enriched):
            row["_source_rank"] = _current_rank(row) or index + 1
            row.setdefault("trade_value_rank", row["_source_rank"])

        total_count = len(enriched)
        context = _config_context(enriched)
        grade_weights = (
            score_structure.get("grade_score_weights")
            if isinstance(score_structure.get("grade_score_weights"), dict)
            else {}
        )
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
            grade, grade_class = grade_for_percent(score)
            grade_text = _config_grade_text(score, self.config)
            items = entry_items + confirmation_items + focus_items
            missing = len([item for item in items if item["status"] == "missing"])
            unknown = len([item for item in items if item["status"] == "unknown"])
            score_status = "partial" if missing else "unknown" if unknown else "ok"

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
                    "score_status": score_status,
                    "score_updated_at": _now_text(),
                    "candidate_score_coverage": (
                        round(
                            len([item for item in items if item["status"] == "ok"])
                            / len(items),
                            2,
                        )
                        if items
                        else None
                    ),
                    "score_breakdown": {
                        "candidate_model": {
                            "id": self.model_id,
                            "name": self.model_name,
                            "entry_score": entry_score,
                            "confirmation_score": confirmation_score,
                            "focus_score": focus_score,
                            "items": items,
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
                        "confirmation_score": {
                            item["key"]: item["points"] for item in confirmation_items
                        },
                        "focus_score": {item["key"]: item["points"] for item in focus_items},
                    },
                    "momentum": self._momentum_text(items),
                    "candidate_reason": self._reason_text(items),
                    "candidate_status": "READY" if score >= 60 else "WEAK",
                }
            )

        return self._rerank_by_score(enriched)

    def _rerank_by_score(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = [dict(row) for row in rows]
        ranked = sorted(
            enriched,
            key=lambda row: (
                -(_number_or_none(row.get("candidate_score")) or 0),
                _number_or_none(row.get("_source_rank")) or float("inf"),
                -(_trade_value(row) or 0),
                str(row.get("stock_code") or ""),
            ),
        )
        for rank, row in enumerate(ranked, start=1):
            previous_rank = _previous_rank(row)
            row["trade_value_rank"] = row.get("rank")
            row["model_rank"] = rank
            row["pool_rank"] = rank
            row["funnel_rank"] = rank
            row["model_rank_change"] = (
                previous_rank - rank if previous_rank is not None else None
            )
            row["pool_stage"] = _pool_stage(rank)
            row["is_candidate"] = rank <= 5
            row["candidate_rank"] = rank if rank <= 5 else None
        return ranked

    def _momentum_text(self, items: list[dict[str, Any]]) -> str:
        labels = [
            str(item.get("label") or item.get("key"))
            for item in items
            if (_number_or_none(item.get("points")) or 0) >= 70
        ]
        return " + ".join(labels[:4]) if labels else "선발신호 약함"

    def _reason_text(self, items: list[dict[str, Any]]) -> str:
        return " + ".join(
            f"{item.get('label') or item.get('key')} {_score_text(item.get('points'))}점"
            for item in items
        )


class FiveFactorFlowV01RankingEngine:
    """Approved five-factor selection model.

    Score factors:
    rank rise, trade-value ratio, execution strength, program net buying,
    and aggregated large trades. Orderbook and one-minute strength are excluded.
    """

    model_id = FIVE_FACTOR_FLOW_V01
    model_name = "5요소 수급선발 v0.1"

    AMOUNT_BANDS = (
        (0.05, 100),
        (0.10, 90),
        (0.20, 80),
        (0.35, 70),
        (0.50, 60),
        (0.70, 40),
        (1.00, 20),
    )
    PROGRAM_BANDS = (
        (0.10, 100),
        (0.20, 85),
        (0.35, 70),
        (0.50, 60),
        (0.70, 45),
        (1.00, 30),
    )
    LARGE_RATIO_BANDS = (
        (0.10, 100),
        (0.20, 90),
        (0.35, 80),
        (0.50, 70),
        (0.70, 50),
        (1.00, 30),
    )

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.model_id = str(self.config.get("id") or self.model_id)
        self.model_name = str(self.config.get("label") or self.config.get("name") or self.model_name)

    def enrich(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = [dict(row) for row in rows]
        amount_values: dict[int, float] = {}
        program_values: dict[int, float] = {}
        large_ratio_values: dict[int, float] = {}

        for index, row in enumerate(enriched):
            trade_value = _trade_value(row)
            previous_value = _previous_trade_value(row)
            if (
                trade_value is not None
                and trade_value > 0
                and previous_value is not None
                and previous_value > 0
            ):
                amount_values[index] = trade_value / previous_value

            program = _program_net(row)
            if trade_value is not None and trade_value > 0 and program is not None and program > 0:
                program_values[index] = program / trade_value

            large_net_sum = _number_or_none(row.get("large_trade_net_sum_eok"))
            if (
                trade_value is not None
                and trade_value > 0
                and large_net_sum is not None
                and large_net_sum > 0
            ):
                large_ratio_values[index] = large_net_sum / trade_value

        amount_scores = _relative_bucket_scores(amount_values, bands=self.AMOUNT_BANDS)
        program_scores = _relative_bucket_scores(program_values, bands=self.PROGRAM_BANDS)
        large_ratio_scores = _relative_bucket_scores(
            large_ratio_values,
            bands=self.LARGE_RATIO_BANDS,
        )

        for index, row in enumerate(enriched):
            rank_score, rank_status, rank_value = self._rank_rise_score(row)
            amount_score = amount_scores.get(index, 0.0)
            amount_status = "ok" if index in amount_values else "missing"
            instant_score, instant_status, instant_value = self._instant_score(row)
            program_score, program_status, program_value = self._program_score(
                row,
                index,
                program_scores,
            )
            large_score, large_status, large_value = self._large_trade_score(
                row,
                index,
                large_ratio_scores,
            )
            combination_score = self._combination_score(
                rank_score,
                amount_score,
                instant_score,
                program_score,
                large_score,
            )

            entry_score = _round_score(rank_score * 0.45 + amount_score * 0.55)
            confirmation_score = _round_score(
                rank_score * 0.10
                + amount_score * 0.25
                + instant_score * 0.25
                + program_score * 0.15
                + large_score * 0.25
            )
            total_score = round(
                rank_score * 0.20
                + amount_score * 0.20
                + instant_score * 0.15
                + program_score * 0.10
                + large_score * 0.10
                + combination_score,
                2,
            )
            total_score = _round_score(total_score)
            grade, grade_class = grade_for_percent(total_score)
            grade_text = grade_text_for_percent(total_score)

            components = [
                self._factor_item(
                    "rank_rise_score",
                    "순위상승",
                    rank_score,
                    20,
                    rank_status,
                    "prev_rank/current_rank",
                    rank_value,
                ),
                self._factor_item(
                    "amount_ratio_score",
                    "대금비",
                    amount_score,
                    20,
                    amount_status,
                    "rank(trade_value/prev_trade_value)",
                    amount_values.get(index),
                ),
                self._factor_item(
                    "instant_strength_score",
                    "순간강도",
                    instant_score,
                    15,
                    instant_status,
                    "execution_strength",
                    instant_value,
                ),
                self._factor_item(
                    "program_score",
                    "프로(억)",
                    program_score,
                    10,
                    program_status,
                    "rank(program_net/trade_value)",
                    program_value,
                ),
                self._factor_item(
                    "large_trade_score",
                    "대량체결",
                    large_score,
                    10,
                    large_status,
                    "large_trade_net_sum/buy_share/count_share",
                    large_value,
                ),
                {
                    "key": "combination_score",
                    "label": "조합품질",
                    "points": combination_score,
                    "weight": 25,
                    "weighted_points": combination_score,
                    "possible_points": 25,
                    "source": "five_factor_alignment",
                    "status": "ok",
                    "value": combination_score,
                },
            ]
            statuses = [
                rank_status,
                amount_status,
                instant_status,
                program_status,
                large_status,
            ]
            coverage = round(len([status for status in statuses if status != "missing"]) / 5, 2)
            score_status = (
                "stale"
                if any(status == "stale" for status in statuses)
                else "partial"
                if any(status == "missing" for status in statuses)
                else "ok"
            )

            row.update(
                {
                    "candidate_model_id": self.model_id,
                    "candidate_model_name": self.model_name,
                    "candidate_score_version": self.model_id,
                    "rank_rise_score": rank_score,
                    "amount_ratio_score": amount_score,
                    "instant_strength_score": instant_score,
                    "program_score": program_score,
                    "large_trade_score": large_score,
                    "combination_score": combination_score,
                    "entry_score": entry_score,
                    "confirmation_score": confirmation_score,
                    "focus_score": total_score,
                    "score_top50": entry_score,
                    "score_top20": confirmation_score,
                    "score_top5": total_score,
                    "candidate_score": total_score,
                    "score_percent": total_score,
                    "grade_score": total_score,
                    "candidate_grade": grade,
                    "candidate_grade_text": grade_text,
                    "candidate_grade_class": grade_class,
                    "display_grade_source": "candidate_model_config",
                    "score_total": total_score,
                    "score_total_points": total_score,
                    "score_possible_points": 100,
                    "score_status": score_status,
                    "score_updated_at": _now_text(),
                    "candidate_score_coverage": coverage,
                    "candidate_status": "READY" if total_score >= 60 else "WEAK",
                    "score_breakdown": {
                        "candidate_model": {
                            "id": self.model_id,
                            "name": self.model_name,
                            "entry_score": entry_score,
                            "confirmation_score": confirmation_score,
                            "focus_score": total_score,
                            "items": components,
                        },
                        "total": {
                            "score": total_score,
                            "possible_points": 100,
                            "percent": total_score,
                            "grade": grade_text,
                            "formula": "20% rank rise + 20% amount ratio + 15% instant strength + 10% program + 10% large trade + 25 combination",
                        },
                    },
                    "candidate_score_items": {
                        "entry_score": {
                            "rank_rise_score": rank_score,
                            "amount_ratio_score": amount_score,
                        },
                        "confirmation_score": {
                            "rank_rise_score": rank_score,
                            "amount_ratio_score": amount_score,
                            "instant_strength_score": instant_score,
                            "program_score": program_score,
                            "large_trade_score": large_score,
                        },
                        "focus_score": {
                            "rank_rise_score": rank_score,
                            "amount_ratio_score": amount_score,
                            "instant_strength_score": instant_score,
                            "program_score": program_score,
                            "large_trade_score": large_score,
                            "combination_score": combination_score,
                        },
                    },
                    "momentum": self._momentum_text(
                        rank_score,
                        amount_score,
                        instant_score,
                        program_score,
                        large_score,
                    ),
                    "candidate_reason": self._reason_text(
                        rank_score,
                        amount_score,
                        instant_score,
                        program_score,
                        large_score,
                        combination_score,
                    ),
                }
            )

        return self._rerank(enriched)

    def _rank_rise_score(self, row: dict[str, Any]) -> tuple[float, str, float | None]:
        current = _current_rank(row)
        previous = _previous_rank(row)
        if current is None or previous is None or current <= 0 or previous <= 0:
            return 0.0, "missing", None

        rise_ratio = (previous - current) / previous
        if rise_ratio <= 0:
            rise_points = 0.0
        elif rise_ratio >= 0.70:
            rise_points = 100.0
        elif rise_ratio >= 0.50:
            rise_points = 90.0
        elif rise_ratio >= 0.30:
            rise_points = 80.0
        elif rise_ratio >= 0.15:
            rise_points = 70.0
        elif rise_ratio >= 0.05:
            rise_points = 60.0
        else:
            rise_points = 40.0

        if current <= 20:
            destination_points = 100.0
        elif current <= 50:
            destination_points = 80.0
        elif current <= 100:
            destination_points = 60.0
        elif current <= 200:
            destination_points = 30.0
        else:
            destination_points = 0.0

        score = _round_score(rise_points * 0.70 + destination_points * 0.30)
        return score, "ok", rise_ratio

    def _instant_score(self, row: dict[str, Any]) -> tuple[float, str, float | None]:
        value = _realtime_strength(row)
        if value is None:
            return 0.0, "missing", None
        age = _age_sec(_first(row, "execution_strength_updated_at", "received_at"))
        if age is not None and age > 3:
            return 0.0, "stale", value
        if value >= 200:
            score = 100
        elif value >= 175:
            score = 90
        elif value >= 150:
            score = 80
        elif value >= 125:
            score = 70
        elif value >= 100:
            score = 60
        elif value >= 90:
            score = 40
        elif value >= 80:
            score = 20
        else:
            score = 0
        return float(score), "ok", value

    def _program_score(
        self,
        row: dict[str, Any],
        index: int,
        program_scores: dict[int, float],
    ) -> tuple[float, str, float | None]:
        value = _program_net(row)
        if value is None:
            return 0.0, "missing", None
        age = _age_sec(row.get("program_net_updated_at"))
        if age is not None and age > 120:
            return 0.0, "stale", value
        if value <= 0:
            return 0.0, "ok", value
        return program_scores.get(index, 0.0), "ok", value

    def _large_trade_score(
        self,
        row: dict[str, Any],
        index: int,
        large_ratio_scores: dict[int, float],
    ) -> tuple[float, str, float | None]:
        buy_count = _number_or_none(row.get("large_trade_buy_count"))
        sell_count = _number_or_none(row.get("large_trade_sell_count"))
        buy_sum = _number_or_none(row.get("large_trade_buy_sum_eok"))
        sell_sum = _number_or_none(row.get("large_trade_sell_sum_eok"))
        net_sum = _number_or_none(row.get("large_trade_net_sum_eok"))

        if all(value is None for value in (buy_count, sell_count, buy_sum, sell_sum, net_sum)):
            return 0.0, "missing", None

        buy_count = buy_count or 0.0
        sell_count = sell_count or 0.0
        buy_sum = buy_sum or 0.0
        sell_sum = sell_sum or 0.0
        net_sum = net_sum if net_sum is not None else buy_sum - sell_sum

        if net_sum <= 0:
            return 0.0, "ok", net_sum

        amount_total = buy_sum + sell_sum
        count_total = buy_count + sell_count
        amount_share = buy_sum / amount_total * 100 if amount_total > 0 else 0.0
        count_share = buy_count / count_total * 100 if count_total > 0 else 0.0
        ratio_score = large_ratio_scores.get(index, 0.0)
        score = _round_score(
            ratio_score * 0.60
            + _round_score(amount_share) * 0.25
            + _round_score(count_share) * 0.15
        )
        if count_total <= 0:
            cap = 0.0
        elif count_total <= 2:
            cap = 60.0
        elif count_total <= 4:
            cap = 80.0
        else:
            cap = 100.0
        return min(score, cap), "ok", net_sum

    def _combination_score(
        self,
        rank_score: float,
        amount_score: float,
        instant_score: float,
        program_score: float,
        large_score: float,
    ) -> float:
        if rank_score >= 70 and amount_score >= 70:
            money_alignment = 8
        elif rank_score >= 60 and amount_score >= 60:
            money_alignment = 5
        elif (
            (rank_score >= 80 and amount_score >= 50)
            or (amount_score >= 80 and rank_score >= 50)
        ):
            money_alignment = 3
        else:
            money_alignment = 0

        flow_confirmation = max(program_score, large_score)
        if instant_score >= 60 and flow_confirmation >= 70:
            execution_confirmation = 7
        elif instant_score >= 60 and flow_confirmation >= 50:
            execution_confirmation = 4
        elif instant_score >= 60:
            execution_confirmation = 1
        else:
            execution_confirmation = 0

        if program_score >= 60 and large_score >= 60:
            dual_flow = 5
        elif max(program_score, large_score) >= 80:
            dual_flow = 3
        elif max(program_score, large_score) >= 60:
            dual_flow = 1
        else:
            dual_flow = 0

        strong_count = len(
            [
                score
                for score in (
                    rank_score,
                    amount_score,
                    instant_score,
                    program_score,
                    large_score,
                )
                if score >= 60
            ]
        )
        strong_points = 5 if strong_count == 5 else 4 if strong_count == 4 else 2 if strong_count == 3 else 0
        return float(money_alignment + execution_confirmation + dual_flow + strong_points)

    def _factor_item(
        self,
        key: str,
        label: str,
        score: float,
        weight: float,
        status: str,
        source: str,
        value: float | None,
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "points": score,
            "weight": weight,
            "weighted_points": round(score * weight / 100, 4),
            "possible_points": weight,
            "source": source,
            "status": status,
            "value": value,
        }

    def _rerank(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ranked = sorted(
            rows,
            key=lambda row: (
                -(_number_or_none(row.get("candidate_score")) or 0),
                _current_rank(row) or float("inf"),
                -(_trade_value(row) or 0),
                str(row.get("stock_code") or ""),
            ),
        )
        for rank, row in enumerate(ranked, start=1):
            row["model_rank"] = rank
            row["pool_rank"] = rank
            row["funnel_rank"] = rank
            row["pool_stage"] = _pool_stage(rank)
            row["is_candidate"] = rank <= 5
            row["candidate_rank"] = rank if rank <= 5 else None
        return ranked

    def _momentum_text(
        self,
        rank_score: float,
        amount_score: float,
        instant_score: float,
        program_score: float,
        large_score: float,
    ) -> str:
        pairs = [
            ("순위상승", rank_score),
            ("대금비", amount_score),
            ("순간강도", instant_score),
            ("프로", program_score),
            ("대량체결", large_score),
        ]
        labels = [label for label, score in pairs if score >= 70]
        return " + ".join(labels[:4]) if labels else "수급선발 약함"

    def _reason_text(
        self,
        rank_score: float,
        amount_score: float,
        instant_score: float,
        program_score: float,
        large_score: float,
        combination_score: float,
    ) -> str:
        return " + ".join(
            (
                f"순위상승 {_score_text(rank_score)}점",
                f"대금비 {_score_text(amount_score)}점",
                f"순간강도 {_score_text(instant_score)}점",
                f"프로 {_score_text(program_score)}점",
                f"대량체결 {_score_text(large_score)}점",
                f"조합 {_score_text(combination_score)}점",
            )
        )


def enrich_candidate_model_fields(
    rows: Iterable[dict[str, Any]],
    model_id: str | None = None,
) -> list[dict[str, Any]]:
    config = load_candidate_model_config(model_id)
    selected_id = str(config.get("id") or model_id or "")
    if selected_id == FIVE_FACTOR_FLOW_V01:
        return FiveFactorFlowV01RankingEngine(config).enrich(rows)
    if selected_id == NET_BUY_STRENGTH_V02 and not isinstance(config.get("score_structure"), dict):
        return NetBuyStrengthV02RankingEngine(config).enrich(rows)
    return ConfigDrivenCandidateRankingEngine(config).enrich(rows)
