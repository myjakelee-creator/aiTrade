"""StockBoard server-side candidate ranking engine.

Only validated, server-side features are used. The browser receives calculated
score/grade/funnel metadata and never calculates candidate scores.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from statistics import median
from typing import Any, Iterable

NET_BUY_STRENGTH_V02 = "NET_BUY_STRENGTH_V02"
FIVE_FACTOR_FLOW_V01 = "FIVE_FACTOR_FLOW_V01"
NET_BUY_STRENGTH_REGULAR_TOTAL_POINTS = 700
NET_BUY_STRENGTH_AFTER_CLOSE_TOTAL_POINTS = 700
NET_BUY_STRENGTH_TOTAL_POINTS = 700
NET_BUY_STRENGTH_FALLBACK_AMOUNT_SCORE = 0
NET_BUY_STRENGTH_MISSING_STRENGTH_SCORE = 0

CANDIDATE_MODEL_DIR = Path(__file__).resolve().parent / "configs" / "candidate_models"
_REGISTRY_CACHE: dict[str, Any] | None = None
_CONFIG_CACHE: dict[str, dict[str, Any]] = {}

FORBIDDEN_MODEL_TOKENS = ("bid_ask", "sell_wall", "strength_1m", "one_min", "vwap", "foreign", "institution")
FEATURE_LABELS = {
    "trade_value_rank": "순위",
    "rank_gap": "순위상승",
    "amount_ratio": "대금비",
    "trade_value_amount": "금액(억)",
    "instant_strength": "순간강도",
    "five_min_strength": "5분강도",
    "program_net": "프로(억)",
    "large_trade": "대량체결",
    "above_open": "시가위",
    "candle_location": "일봉위치",
    "near_high": "고가권",
    "upper_wick_ratio": "윗꼬리",
    "not_overheated": "적정등락률",
    "relative_change": "보드상대강도",
    "green_while_board_weak": "약세보드양봉",
    "strength_alignment": "강도일치",
    "combination_quality": "조합품질",
    "reversal_penalty": "반전위험",
    "strength_divergence_penalty": "강도괴리",
    "program_plus_strength": "프로+강도",
    "flow_confirmation": "수급확인",
}
SUPPORTED_FEATURE_KEYS = frozenset(FEATURE_LABELS)
PENALTY_FEATURE_KEYS = frozenset({"reversal_penalty", "strength_divergence_penalty"})
SUPPORTED_GUARD_TYPES = frozenset({
    "feature_value_min", "feature_value_max", "feature_score_min", "any_value_min",
    "any_positive", "all_nonnegative",
})


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
    return float(number) if number.is_finite() else None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _round_score(value: float | None) -> float:
    return round(_clamp(value or 0.0), 2)


def _score_text(value: Any) -> str:
    number = _number_or_none(value)
    if number is None:
        return "-"
    return str(int(number)) if number == int(number) else f"{number:.2f}".rstrip("0").rstrip(".")


def _now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _age_sec(value: Any) -> float | None:
    parsed = _parse_datetime(value)
    return max(0.0, (datetime.now() - parsed).total_seconds()) if parsed else None


def grade_for_percent(score: int | float | None) -> tuple[str | None, str]:
    if score is None:
        return None, ""
    number = int(round(_clamp(float(score))))
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
    grade, _ = grade_for_percent(score)
    return "-" if grade is None or score is None else f"{grade}{int(round(_clamp(float(score))))}"


def _current_rank(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "rank", "displayed_rank", "current_rank"))


def _previous_rank(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "prev_rank", "previous_rank", "pred_rank"))


def _trade_value(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "trade_value_eok", "realtime_acc_trade_value_eok_candidate"))


def _previous_trade_value(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "prev_trade_value_eok", "previous_trade_value_eok"))


def _realtime_strength(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "execution_strength", "realtime_strength"))


def _five_min_strength(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "strength_5m", "five_min_strength"))


def _program_net(row: dict[str, Any]) -> float | None:
    return _number_or_none(_first(row, "program_net", "program_sum", "program_net_eok"))


def _row_ohlc_value(row: dict[str, Any], key: str) -> float | None:
    ohlc = row.get("ohlc")
    if isinstance(ohlc, dict):
        value = _number_or_none(ohlc.get(key))
        if value is not None:
            return value
    return _number_or_none(row.get(f"day_{key}") or row.get(key))


def _rank_position_score(position: int | None, total_count: int) -> float:
    if position is None or total_count <= 0:
        return 0.0
    if total_count == 1:
        return 100.0
    return _round_score(100 * (total_count - position) / (total_count - 1))


def _rank_value_scores(values: dict[int, float], total_count: int) -> dict[int, float]:
    if not values or total_count <= 0:
        return {}
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    result: dict[int, float] = {}
    last_value: float | None = None
    last_position = 0
    for ordinal, (index, value) in enumerate(ordered, start=1):
        if last_value is None or value != last_value:
            last_value, last_position = value, ordinal
        result[index] = _rank_position_score(last_position, max(total_count, len(ordered)))
    return result


def _strength_score(value: Any) -> float:
    number = _number_or_none(value)
    if number is None or number <= 70:
        return 0.0
    points = ((70, 0), (90, 30), (100, 55), (120, 75), (150, 90), (200, 100))
    if number >= 200:
        return 100.0
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if left_x <= number <= right_x:
            ratio = (number - left_x) / (right_x - left_x)
            return _round_score(left_y + ratio * (right_y - left_y))
    return 0.0


def _rank_rise_score(row: dict[str, Any]) -> tuple[float, float | None, str]:
    current, previous = _current_rank(row), _previous_rank(row)
    if current is None or previous is None or current <= 0 or previous <= 0:
        return 0.0, None, "missing"
    ratio = (previous - current) / previous
    if ratio <= 0:
        rise = 0.0
    elif ratio >= 0.70:
        rise = 100.0
    elif ratio >= 0.50:
        rise = 90.0
    elif ratio >= 0.30:
        rise = 80.0
    elif ratio >= 0.15:
        rise = 70.0
    elif ratio >= 0.05:
        rise = 60.0
    else:
        rise = 40.0
    destination = 100 if current <= 20 else 80 if current <= 50 else 60 if current <= 100 else 30 if current <= 200 else 0
    return _round_score(rise * 0.70 + destination * 0.30), ratio, "ok"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _raw_registry() -> dict[str, Any]:
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = _read_json(CANDIDATE_MODEL_DIR / "_registry.json") or {"models": []}
    return dict(_REGISTRY_CACHE)


def validate_candidate_model_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not str(config.get("id") or ""):
        errors.append("model_id_missing")
    structure = config.get("score_structure")
    if not isinstance(structure, dict):
        return [*errors, "score_structure_missing"]
    for group_name in ("final_score", "entry_score", "confirmation_score", "focus_score"):
        items = structure.get(group_name)
        if not isinstance(items, list) or not items:
            errors.append(f"{group_name}_missing")
            continue
        positive_total = 0.0
        for item in items:
            if not isinstance(item, dict):
                errors.append(f"{group_name}_invalid_item")
                continue
            key = str(item.get("key") or "")
            weight = _number_or_none(item.get("weight"))
            if key not in SUPPORTED_FEATURE_KEYS:
                errors.append(f"unsupported_feature:{key}")
            if any(token in key.lower() for token in FORBIDDEN_MODEL_TOKENS):
                errors.append(f"forbidden_feature:{key}")
            if weight is None or weight == 0:
                errors.append(f"invalid_weight:{group_name}:{key}")
            elif weight > 0:
                positive_total += weight
            elif key not in PENALTY_FEATURE_KEYS:
                errors.append(f"negative_weight_not_penalty:{key}")
        if abs(positive_total - 100.0) > 0.001:
            errors.append(f"positive_weight_total:{group_name}:{positive_total}")
    for key in config.get("required_features") or []:
        if str(key) not in SUPPORTED_FEATURE_KEYS:
            errors.append(f"unsupported_required_feature:{key}")
    for guard in config.get("grade_guards") or []:
        if not isinstance(guard, dict) or str(guard.get("type") or "") not in SUPPORTED_GUARD_TYPES:
            errors.append("unsupported_guard")
            continue
        keys = guard.get("keys") if isinstance(guard.get("keys"), list) else [guard.get("key")]
        for key in keys:
            if key and str(key) not in SUPPORTED_FEATURE_KEYS:
                errors.append(f"unsupported_guard_feature:{key}")
    return sorted(set(errors))


def load_candidate_model_registry(*, include_configs: bool = False) -> dict[str, Any]:
    raw = _raw_registry()
    valid_models: list[dict[str, Any]] = []
    invalid_models: list[dict[str, Any]] = []
    configs: dict[str, dict[str, Any]] = {}
    for item in raw.get("models") or []:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get("file") or "")
        config = _read_json(CANDIDATE_MODEL_DIR / file_name) if file_name else None
        config = dict(config or {})
        config.setdefault("id", item.get("id"))
        config.setdefault("label", item.get("label"))
        errors = validate_candidate_model_config(config)
        if errors:
            invalid_models.append({**item, "validation_errors": errors})
            continue
        valid_models.append(dict(item))
        if include_configs:
            configs[str(item.get("id"))] = config
    result = {**raw, "models": valid_models, "invalid_models": invalid_models}
    if include_configs:
        result["configs"] = configs
    if not any(str(item.get("id")) == str(result.get("default_model_id")) for item in valid_models):
        result["default_model_id"] = valid_models[0].get("id") if valid_models else FIVE_FACTOR_FLOW_V01
    return result


def load_candidate_model_config(model_id: str | None = None) -> dict[str, Any]:
    raw = _raw_registry()
    default_id = str(raw.get("default_model_id") or FIVE_FACTOR_FLOW_V01)
    requested = str(model_id or default_id)
    models = [item for item in raw.get("models") or [] if isinstance(item, dict)]
    item = next((item for item in models if str(item.get("id")) == requested), None)
    if item is None and requested != default_id:
        return load_candidate_model_config(default_id)
    if item is None:
        raise ValueError("candidate model registry is empty")
    selected_id = str(item.get("id"))
    if selected_id in _CONFIG_CACHE:
        return dict(_CONFIG_CACHE[selected_id])
    config = _read_json(CANDIDATE_MODEL_DIR / str(item.get("file") or "")) or {}
    config.setdefault("id", selected_id)
    config.setdefault("label", item.get("label") or selected_id)
    config.setdefault("name", config.get("label"))
    errors = validate_candidate_model_config(config)
    if errors:
        if selected_id != default_id:
            return load_candidate_model_config(default_id)
        raise ValueError(f"invalid default candidate model {selected_id}: {errors}")
    config["validation_status"] = "READY"
    _CONFIG_CACHE[selected_id] = dict(config)
    return config


