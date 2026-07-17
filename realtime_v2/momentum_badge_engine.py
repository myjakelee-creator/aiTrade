from __future__ import annotations

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from realtime_v2.common import normalize_code, to_number

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "stockboard_momentum_badges.json"
ALLOWED_VARIABLES = frozenset({"O", "H", "L", "C", "C1", "V", "V1", "dayOpen"})
ALLOWED_OPERATORS = frozenset({">", "<", ">=", "<=", "=="})


def _number(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    try:
        result = float(number)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _minute(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


@dataclass(frozen=True)
class Condition:
    left: str
    operator: str
    right: str

    def matches(self, values: dict[str, float | None]) -> bool:
        left = values.get(self.left)
        right = values.get(self.right)
        if left is None or right is None:
            return False
        if self.operator == ">":
            return left > right
        if self.operator == "<":
            return left < right
        if self.operator == ">=":
            return left >= right
        if self.operator == "<=":
            return left <= right
        return left == right


@dataclass(frozen=True)
class CompiledRule:
    rule_id: str
    reference: str
    badge: str
    label: str
    tone: str
    priority: int
    requires_previous: bool
    trigger_conditions: tuple[Condition, ...]
    stay_conditions: tuple[Condition, ...]

    def triggers(self, values: dict[str, float | None], has_previous: bool) -> bool:
        if self.requires_previous and not has_previous:
            return False
        return bool(self.trigger_conditions) and all(
            condition.matches(values) for condition in self.trigger_conditions
        )

    def stays(self, values: dict[str, float | None]) -> bool:
        return bool(self.stay_conditions) and all(
            condition.matches(values) for condition in self.stay_conditions
        )


def _load_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("momentum badge config must be a JSON object")
    return payload


def _compile_conditions(
    raw_conditions: Any,
    *,
    rule_id: str,
    field: str,
) -> tuple[Condition, ...]:
    conditions: list[Condition] = []
    for raw_condition in raw_conditions or []:
        if not isinstance(raw_condition, dict):
            raise ValueError(f"momentum rule {rule_id} has an invalid {field} condition")
        left = str(raw_condition.get("left") or "")
        operator = str(raw_condition.get("op") or "")
        right = str(raw_condition.get("right") or "")
        if left not in ALLOWED_VARIABLES or right not in ALLOWED_VARIABLES:
            raise ValueError(f"momentum rule {rule_id} uses an unsupported variable")
        if operator not in ALLOWED_OPERATORS:
            raise ValueError(f"momentum rule {rule_id} uses an unsupported operator")
        conditions.append(Condition(left, operator, right))
    if not conditions:
        raise ValueError(f"momentum rule {rule_id} has no {field} conditions")
    return tuple(conditions)


def _compile_rules(payload: dict[str, Any]) -> tuple[CompiledRule, ...]:
    compiled: list[CompiledRule] = []
    seen_ids: set[str] = set()
    for raw in payload.get("rules") or []:
        if not isinstance(raw, dict):
            raise ValueError("each momentum rule must be an object")
        rule_id = str(raw.get("id") or "").strip()
        reference = str(raw.get("reference") or "").strip()
        badge = str(raw.get("badge") or "").strip()
        label = str(raw.get("label") or badge).strip()
        tone = str(raw.get("tone") or "neutral").strip()
        if not rule_id or rule_id in seen_ids:
            raise ValueError(f"invalid or duplicate momentum rule id: {rule_id!r}")
        if reference not in {"open", "vwap"}:
            raise ValueError(f"unsupported momentum reference: {reference!r}")
        if not badge:
            raise ValueError(f"momentum rule {rule_id} has no badge")
        trigger_conditions = _compile_conditions(
            raw.get("all"),
            rule_id=rule_id,
            field="trigger",
        )
        stay_conditions = _compile_conditions(
            raw.get("stay") or raw.get("all"),
            rule_id=rule_id,
            field="stay",
        )
        seen_ids.add(rule_id)
        compiled.append(
            CompiledRule(
                rule_id=rule_id,
                reference=reference,
                badge=badge,
                label=label,
                tone=tone,
                priority=int(raw.get("priority") or 0),
                requires_previous=bool(raw.get("requires_previous")),
                trigger_conditions=trigger_conditions,
                stay_conditions=stay_conditions,
            )
        )
    if not compiled:
        raise ValueError("momentum badge config contains no rules")
    return tuple(sorted(compiled, key=lambda rule: (-rule.priority, rule.rule_id)))


def load_momentum_badge_config(path: Path | None = None) -> dict[str, Any]:
    config_path = Path(path or DEFAULT_CONFIG_PATH)
    payload = _load_payload(config_path)
    rules = _compile_rules(payload)
    reference_order = tuple(
        str(item) for item in payload.get("reference_order") or ("open", "vwap")
    )
    if len(reference_order) != 2 or set(reference_order) != {"open", "vwap"}:
        raise ValueError("reference_order must contain open and vwap exactly once")
    exit_hold = max(0, int(payload.get("exit_hold_minutes") or 0))
    fade = max(0, int(payload.get("fade_minutes") or 0))
    if exit_hold + fade <= 0:
        raise ValueError("exit hold and fade duration cannot both be zero")
    display = payload.get("display") if isinstance(payload.get("display"), dict) else {}
    top_alert = (
        payload.get("top_alert") if isinstance(payload.get("top_alert"), dict) else {}
    )
    return {
        "path": str(config_path),
        "schema_version": int(payload.get("schema_version") or 1),
        "enabled": bool(payload.get("enabled", True)),
        "exit_hold_minutes": exit_hold,
        "fade_minutes": fade,
        "reference_order": reference_order,
        "display": {
            "replace_grade_badge": bool(display.get("replace_grade_badge", True)),
            "dual_badge_mode": str(display.get("dual_badge_mode") or "alternate"),
            "alternate_interval_ms": max(
                400,
                int(display.get("alternate_interval_ms") or 1200),
            ),
        },
        "top_alert": {
            "enabled": bool(top_alert.get("enabled", True)),
            "page_size": max(1, min(10, int(top_alert.get("page_size") or 4))),
            "rotate_interval_ms": max(
                500,
                int(top_alert.get("rotate_interval_ms") or 1800),
            ),
            "max_items": max(1, min(1000, int(top_alert.get("max_items") or 300))),
        },
        "rules": rules,
    }


class MomentumBadgeEngine:
    """Evaluate configured rules only when a completed one-minute candle changes."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.rules: tuple[CompiledRule, ...] = tuple(config["rules"])
        self.rules_by_reference = {
            reference: tuple(
                rule for rule in self.rules if rule.reference == reference
            )
            for reference in config["reference_order"]
        }
        self.rule_by_id = {rule.rule_id: rule for rule in self.rules}
        self.rule_by_label = {rule.label: rule for rule in self.rules}
        self.states: dict[str, dict[str, dict[str, Any]]] = {}
        self.last_candle_by_code: dict[str, dict[str, Any]] = {}
        self.version = 0

    def _context(
        self,
        current: dict[str, Any],
        previous: dict[str, Any] | None,
        day_open: Any,
    ) -> dict[str, float | None]:
        return {
            "O": _number(current.get("open")),
            "H": _number(current.get("high")),
            "L": _number(current.get("low")),
            "C": _number(current.get("close")),
            "V": _number(current.get("vwap")),
            "C1": _number(previous.get("close")) if isinstance(previous, dict) else None,
            "V1": _number(previous.get("vwap")) if isinstance(previous, dict) else None,
            "dayOpen": _number(day_open),
        }

    def seed_last_candle(self, code: Any, candle: dict[str, Any] | None) -> None:
        normalized = normalize_code(code)
        if normalized and isinstance(candle, dict) and _minute(candle.get("minute_key")):
            self.last_candle_by_code[normalized] = deepcopy(candle)

    def restore_code(
        self,
        code: Any,
        payload: dict[str, Any] | None,
        trading_date: str,
    ) -> None:
        normalized = normalize_code(code)
        if not normalized or not isinstance(payload, dict):
            return
        if str(payload.get("trading_date") or "") != str(trading_date or ""):
            return
        restored: dict[str, dict[str, Any]] = {}
        for reference in self.config["reference_order"]:
            raw = payload.get(reference)
            if not isinstance(raw, dict):
                continue
            rule = self.rule_by_id.get(str(raw.get("rule_id") or ""))
            if rule is None or rule.reference != reference:
                continue
            restored[reference] = {
                "rule_id": rule.rule_id,
                "reference": reference,
                "badge": rule.badge,
                "label": rule.label,
                "tone": rule.tone,
                "signal_minute": _minute(raw.get("signal_minute")),
                "last_matched_minute": _minute(raw.get("last_matched_minute")),
                "exit_minute": _minute(raw.get("exit_minute")),
                "fade_start_minute": _minute(raw.get("fade_start_minute")),
                "expires_minute": _minute(raw.get("expires_minute")),
                "trading_date": str(trading_date),
            }
        if restored:
            self.states[normalized] = restored

    def serialize_code(self, code: Any, trading_date: str) -> dict[str, Any]:
        normalized = normalize_code(code)
        references = deepcopy(self.states.get(normalized) or {})
        return {
            "schema_version": int(self.config["schema_version"]),
            "trading_date": str(trading_date or ""),
            **references,
        }

    def _legacy_rule(
        self,
        reference: str,
        current_minute: int,
        legacy_signals: dict[str, Any] | None,
    ) -> CompiledRule | None:
        if not isinstance(legacy_signals, dict):
            return None
        raw = legacy_signals.get(reference)
        if not isinstance(raw, dict) or _minute(raw.get("minute")) != current_minute:
            return None
        rule = self.rule_by_label.get(str(raw.get("label") or ""))
        return rule if rule is not None and rule.reference == reference else None

    def _activate(
        self,
        reference: str,
        rule: CompiledRule,
        existing: dict[str, Any] | None,
        current_minute: int,
        trading_date: str,
    ) -> tuple[dict[str, Any], bool]:
        same_rule = isinstance(existing, dict) and existing.get("rule_id") == rule.rule_id
        already_active = same_rule and existing.get("exit_minute") is None
        if already_active:
            existing["last_matched_minute"] = current_minute
            existing["trading_date"] = str(trading_date or "")
            return existing, False
        next_state = {
            "rule_id": rule.rule_id,
            "reference": reference,
            "badge": rule.badge,
            "label": rule.label,
            "tone": rule.tone,
            "signal_minute": (
                _minute(existing.get("signal_minute")) or current_minute
                if same_rule and isinstance(existing, dict)
                else current_minute
            ),
            "last_matched_minute": current_minute,
            "exit_minute": None,
            "fade_start_minute": None,
            "expires_minute": None,
            "trading_date": str(trading_date or ""),
        }
        return next_state, existing != next_state

    def observe_completed_candle(
        self,
        code: Any,
        current: dict[str, Any] | None,
        *,
        day_open: Any,
        trading_date: str,
        legacy_signals: dict[str, Any] | None = None,
    ) -> bool:
        normalized = normalize_code(code)
        if not self.config["enabled"] or not normalized or not isinstance(current, dict):
            return False
        current_minute = _minute(current.get("minute_key"))
        if current_minute is None or bool(current.get("partial")):
            return False
        previous = self.last_candle_by_code.get(normalized)
        if previous is not None and _minute(previous.get("minute_key")) == current_minute:
            return False
        values = self._context(current, previous, day_open)
        state = self.states.setdefault(normalized, {})
        changed = False
        for reference in self.config["reference_order"]:
            triggered = next(
                (
                    rule
                    for rule in self.rules_by_reference[reference]
                    if rule.triggers(values, previous is not None)
                ),
                None,
            )
            if triggered is None and previous is None:
                triggered = self._legacy_rule(
                    reference,
                    current_minute,
                    legacy_signals,
                )
            existing = state.get(reference)
            if triggered is not None:
                next_state, state_changed = self._activate(
                    reference,
                    triggered,
                    existing,
                    current_minute,
                    trading_date,
                )
                state[reference] = next_state
                changed = state_changed or changed
                continue

            existing_rule = (
                self.rule_by_id.get(str(existing.get("rule_id") or ""))
                if isinstance(existing, dict)
                else None
            )
            if existing_rule is not None and existing_rule.stays(values):
                next_state, state_changed = self._activate(
                    reference,
                    existing_rule,
                    existing,
                    current_minute,
                    trading_date,
                )
                state[reference] = next_state
                changed = state_changed or changed
                continue

            if isinstance(existing, dict) and existing.get("exit_minute") is None:
                exit_minute = current_minute
                state[reference] = {
                    **existing,
                    "exit_minute": exit_minute,
                    "fade_start_minute": exit_minute
                    + int(self.config["exit_hold_minutes"]),
                    "expires_minute": exit_minute
                    + int(self.config["exit_hold_minutes"])
                    + int(self.config["fade_minutes"]),
                }
                changed = True
        if not state:
            self.states.pop(normalized, None)
        self.last_candle_by_code[normalized] = deepcopy(current)
        if changed:
            self.version += 1
        return changed

    def expire(self, current_minute: int | None) -> set[str]:
        if current_minute is None:
            return set()
        changed_codes: set[str] = set()
        for code, references in tuple(self.states.items()):
            for reference, state in tuple(references.items()):
                expires = _minute(state.get("expires_minute"))
                if expires is not None and current_minute >= expires:
                    references.pop(reference, None)
                    changed_codes.add(code)
            if not references:
                self.states.pop(code, None)
        if changed_codes:
            self.version += 1
        return changed_codes

    def badges(self, code: Any, current_minute: int | None) -> list[dict[str, Any]]:
        normalized = normalize_code(code)
        result: list[dict[str, Any]] = []
        references = self.states.get(normalized) or {}
        for reference in self.config["reference_order"]:
            state = references.get(reference)
            if not isinstance(state, dict):
                continue
            fade_start = _minute(state.get("fade_start_minute"))
            exit_minute = _minute(state.get("exit_minute"))
            if exit_minute is None:
                phase = "active"
            elif (
                current_minute is not None
                and fade_start is not None
                and current_minute >= fade_start
            ):
                phase = "fading"
            else:
                phase = "grace"
            result.append(
                {
                    "id": state.get("rule_id"),
                    "reference": reference,
                    "badge": state.get("badge"),
                    "label": state.get("label"),
                    "tone": state.get("tone"),
                    "phase": phase,
                    "signal_minute": state.get("signal_minute"),
                    "last_matched_minute": state.get("last_matched_minute"),
                }
            )
        return result

    def alert_rows(
        self,
        metadata_by_code: dict[str, dict[str, Any]],
        current_minute: int | None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for code in self.states:
            badges = self.badges(code, current_minute)
            if not badges:
                continue
            metadata = metadata_by_code.get(code) or {}
            rank = _number(metadata.get("rank"))
            result.append(
                {
                    "stock_code": code,
                    "stock_name": metadata.get("stock_name") or code,
                    "rank": int(rank) if rank is not None and rank > 0 else 999999,
                    "badges": badges,
                    "latest_signal_minute": max(
                        (
                            _minute(item.get("signal_minute")) or 0
                            for item in badges
                        ),
                        default=0,
                    ),
                }
            )
        result.sort(
            key=lambda item: (
                -int(item["latest_signal_minute"]),
                int(item["rank"]),
                item["stock_code"],
            )
        )
        return result[: int(self.config["top_alert"]["max_items"])]
