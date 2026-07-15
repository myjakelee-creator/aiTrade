from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from realtime_v2.board_data_hub import BoardDataHub
from realtime_v2.board_projection_runtime import LatestOnlyProjectionWorker
from realtime_v2.common import now_text


ROOT = Path(__file__).resolve().parents[1]


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _code(value: Any) -> str:
    text = "".join(ch for ch in str(value or "") if ch.isdigit())
    return text[-6:] if len(text) >= 6 else ""


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _weighted_average(items: list[tuple[float | None, float]]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for value, weight in items:
        if value is None or weight <= 0:
            continue
        numerator += float(value) * float(weight)
        denominator += float(weight)
    return numerator / denominator if denominator > 0 else None


def _rank_percent(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    if not ordered:
        return {}
    if len(ordered) == 1:
        return {ordered[0][0]: 100.0}
    denominator = len(ordered) - 1
    return {
        key: round((denominator - index) / denominator * 100.0, 4)
        for index, (key, _value) in enumerate(ordered)
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _tone(value: float | None) -> str:
    if value is None or value == 0:
        return "zero"
    return "plus" if value > 0 else "minus"


def _fmt_number(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:,.{digits}f}"


def _fmt_eok(value: float | None) -> str:
    return "-" if value is None else f"{value:,.1f}억"


def _fmt_pct(value: float | None, digits: int = 0) -> str:
    if value is None:
        return "-"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.{digits}f}%"


def _first_number(row: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(row.get(key))
        if value is not None:
            return value
    return None


class ThemeMembershipLoader:
    """Load one local theme master without making any OpenAPI request."""

    def __init__(self, paths: list[Path] | None = None, refresh_sec: float = 5.0):
        env_path = os.getenv("STOCKBOARD_THEME_MEMBERSHIP_FILE", "").strip()
        default_paths = [
            ROOT / "config" / "stockboard_theme_master.json",
            ROOT / "data" / "runtime" / "stockboard_v2" / "theme_membership.json",
            ROOT / "data" / "config" / "theme_membership.json",
            ROOT / "docs" / "assets" / "theme_membership.json",
        ]
        self.paths = paths or ([Path(env_path)] if env_path else []) + default_paths
        self.refresh_sec = max(1.0, float(refresh_sec))
        self._last_check_mono = 0.0
        self._last_mtime: float | None = None
        self._source: str | None = None
        self._themes: list[dict[str, Any]] = []
        self._master_version: str | None = None
        self._last_error: str | None = None

    def _parse_weighted_master(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        definitions = payload.get("themes")
        memberships = payload.get("stock_memberships")
        if not isinstance(definitions, dict) or not isinstance(memberships, dict):
            return []

        by_theme: dict[str, list[dict[str, Any]]] = {
            str(theme_id): [] for theme_id in definitions
        }
        for raw_code, raw_memberships in memberships.items():
            code = _code(raw_code)
            if not code or not isinstance(raw_memberships, list):
                continue
            for raw_membership in raw_memberships:
                if not isinstance(raw_membership, dict):
                    continue
                theme_id = str(raw_membership.get("theme_id") or "").strip()
                if theme_id not in definitions:
                    continue
                weight = _number(raw_membership.get("weight"))
                if weight is None or weight <= 0:
                    continue
                by_theme.setdefault(theme_id, []).append(
                    {
                        "stock_code": code,
                        "weight": round(float(weight), 6),
                        "relation": str(
                            raw_membership.get("relation") or "related"
                        ).strip(),
                    }
                )

        themes: list[dict[str, Any]] = []
        for theme_id, raw_definition in definitions.items():
            definition = raw_definition if isinstance(raw_definition, dict) else {}
            if definition.get("enabled") is False:
                continue
            members = sorted(
                by_theme.get(str(theme_id), []),
                key=lambda item: item["stock_code"],
            )
            name = str(definition.get("theme_name") or theme_id).strip()
            if not name or not members:
                continue
            themes.append(
                {
                    "theme_id": str(theme_id),
                    "theme_name": name,
                    "short_name": str(
                        definition.get("short_name") or name
                    ).strip(),
                    "min_active_members": max(
                        1, int(_number(definition.get("min_active_members")) or 1)
                    ),
                    "members": members,
                }
            )
        return themes

    def _parse_simple_master(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        themes: list[dict[str, Any]] = []
        raw_themes = payload.get("themes")
        if isinstance(raw_themes, list):
            for index, item in enumerate(raw_themes, start=1):
                if not isinstance(item, dict):
                    continue
                raw_members = item.get("members") or item.get("stock_codes") or []
                members: list[dict[str, Any]] = []
                for raw_member in raw_members:
                    if isinstance(raw_member, dict):
                        code = _code(
                            raw_member.get("stock_code") or raw_member.get("code")
                        )
                        weight = _number(raw_member.get("weight")) or 1.0
                        relation = str(
                            raw_member.get("relation") or raw_member.get("role") or "related"
                        )
                    else:
                        code = _code(raw_member)
                        weight = 1.0
                        relation = "related"
                    if code:
                        members.append(
                            {
                                "stock_code": code,
                                "weight": round(float(weight), 6),
                                "relation": relation,
                            }
                        )
                name = str(item.get("theme_name") or item.get("name") or "").strip()
                theme_id = str(
                    item.get("theme_id") or item.get("id") or name or index
                ).strip()
                if name and members:
                    themes.append(
                        {
                            "theme_id": theme_id,
                            "theme_name": name,
                            "short_name": str(item.get("short_name") or name),
                            "min_active_members": max(
                                1, int(_number(item.get("min_active_members")) or 1)
                            ),
                            "members": sorted(
                                members, key=lambda member: member["stock_code"]
                            ),
                        }
                    )

        stock_to_themes = payload.get("stock_to_themes") or payload.get("membership")
        if isinstance(stock_to_themes, dict):
            by_name: dict[str, set[str]] = {}
            for raw_code, raw_names in stock_to_themes.items():
                code = _code(raw_code)
                if not code:
                    continue
                names = raw_names if isinstance(raw_names, list) else [raw_names]
                for raw_name in names:
                    name = str(raw_name or "").strip()
                    if name:
                        by_name.setdefault(name, set()).add(code)
            existing = {item["theme_name"] for item in themes}
            for name, codes in sorted(by_name.items()):
                if name in existing or not codes:
                    continue
                themes.append(
                    {
                        "theme_id": name,
                        "theme_name": name,
                        "short_name": name,
                        "min_active_members": 1,
                        "members": [
                            {
                                "stock_code": code,
                                "weight": 1.0,
                                "relation": "related",
                            }
                            for code in sorted(codes)
                        ],
                    }
                )
        return themes

    def _parse(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        weighted = self._parse_weighted_master(payload)
        return weighted or self._parse_simple_master(payload)

    def load(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        now_mono = time.monotonic()
        if now_mono - self._last_check_mono < self.refresh_sec:
            return self._themes, self.status()
        self._last_check_mono = now_mono

        source: Path | None = None
        for path in self.paths:
            try:
                if path.is_file():
                    source = path
                    break
            except OSError:
                continue

        if source is None:
            self._source = None
            self._themes = []
            self._last_mtime = None
            self._master_version = None
            self._last_error = None
            return self._themes, self.status()

        try:
            mtime = source.stat().st_mtime
            if self._source == str(source) and self._last_mtime == mtime:
                return self._themes, self.status()
            payload = json.loads(source.read_text(encoding="utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("theme membership root must be an object")
            themes = self._parse(payload)
            if not themes:
                raise ValueError("theme master contains no valid themes")
            self._themes = themes
            self._source = str(source)
            self._last_mtime = mtime
            self._master_version = str(
                payload.get("master_version") or payload.get("schema_version") or ""
            )
            self._last_error = None
        except Exception as error:
            self._last_error = f"{type(error).__name__}: {error}"
        return self._themes, self.status()

    def status(self) -> dict[str, Any]:
        return {
            "source": self._source,
            "master_version": self._master_version,
            "theme_count": len(self._themes),
            "member_count": sum(
                len(theme.get("members") or []) for theme in self._themes
            ),
            "last_error": self._last_error,
            "candidate_paths": [str(path) for path in self.paths],
        }


class ThemeProjectionBuilder:
    """Aggregate one shared feature snapshot into display-ready ThemeBoard rows."""

    def __init__(self, loader: ThemeMembershipLoader | None = None) -> None:
        self.loader = loader or ThemeMembershipLoader()

    def _member_row(
        self,
        row: dict[str, Any],
        member: dict[str, Any],
    ) -> dict[str, Any]:
        candidate_score = _first_number(row, "candidate_score", "grade_score")
        change_rate = _number(row.get("change_rate"))
        trade_value = _number(row.get("trade_value_eok"))
        one_min = _first_number(row, "trade_value_1m_eok", "one_min_trade_value_eok")
        five_min = _first_number(row, "trade_value_5m_eok", "five_min_trade_value_eok")
        program_net = _number(row.get("program_net"))
        large_net = _number(row.get("large_trade_net_sum_eok"))
        large_count = _number(row.get("large_trade_net_count"))
        amount_ratio = _first_number(row, "amount_ratio", "trade_value_ratio")
        execution_strength = _number(row.get("execution_strength"))
        strength_5m = _number(row.get("strength_5m"))
        price = _first_number(row, "price", "trade_price")

        leadership_score = 0.0
        leadership_weight = 0.0
        if candidate_score is not None:
            leadership_score += _clamp(candidate_score) * 45
            leadership_weight += 45
        if change_rate is not None:
            leadership_score += _clamp((change_rate + 5.0) * 10.0) * 20
            leadership_weight += 20
        if amount_ratio is not None:
            leadership_score += _clamp(amount_ratio * 20.0) * 15
            leadership_weight += 15
        if execution_strength is not None:
            leadership_score += _clamp((execution_strength - 70.0) / 1.3) * 10
            leadership_weight += 10
        flow_signal = 0.0
        flow_ready = False
        if program_net is not None:
            flow_signal += _clamp(50.0 + program_net * 2.0)
            flow_ready = True
        if large_net is not None:
            flow_signal += _clamp(50.0 + large_net * 10.0)
            flow_ready = True
        if flow_ready:
            divisor = int(program_net is not None) + int(large_net is not None)
            leadership_score += (flow_signal / max(1, divisor)) * 10
            leadership_weight += 10
        normalized_leadership = (
            leadership_score / leadership_weight if leadership_weight > 0 else 0.0
        )

        return {
            "stock_code": _code(row.get("stock_code")),
            "stock_name": row.get("stock_name") or _code(row.get("stock_code")),
            "master_weight": round(float(member.get("weight") or 1.0), 6),
            "master_relation": str(member.get("relation") or "related"),
            "price": price,
            "price_text": "-" if price is None else f"{price:,.0f}",
            "change_rate": change_rate,
            "change_rate_text": _fmt_pct(change_rate, 2),
            "change_rate_tone": _tone(change_rate),
            "trade_value_eok": trade_value,
            "trade_value_text": _fmt_eok(trade_value),
            "trade_value_1m_eok": one_min,
            "trade_value_1m_text": _fmt_eok(one_min),
            "trade_value_5m_eok": five_min,
            "trade_value_5m_text": _fmt_eok(five_min),
            "amount_ratio": amount_ratio,
            "amount_ratio_text": _fmt_number(amount_ratio, 2),
            "execution_strength": execution_strength,
            "execution_strength_text": _fmt_number(execution_strength, 0),
            "strength_5m": strength_5m,
            "strength_5m_text": _fmt_number(strength_5m, 0),
            "program_net": program_net,
            "program_net_text": _fmt_eok(program_net),
            "program_net_tone": _tone(program_net),
            "large_trade_net_sum_eok": large_net,
            "large_trade_net_count": large_count,
            "large_trade_text": _fmt_eok(large_net),
            "large_trade_tone": _tone(large_net),
            "candidate_score": candidate_score,
            "candidate_score_text": _fmt_number(candidate_score, 1),
            "candidate_grade": str(
                row.get("candidate_grade")
                or row.get("grade")
                or (_grade(candidate_score) if candidate_score is not None else "-")
            ),
            "leadership_score": round(normalized_leadership, 2),
            "price_age_sec": _number(row.get("price_age_sec")),
            "received_at": row.get("received_at"),
            "row_source": row.get("row_source"),
        }

    def _aggregate_theme(
        self,
        theme: dict[str, Any],
        by_code: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        members = theme.get("members") or []
        active: list[dict[str, Any]] = []
        for member in members:
            code = _code(member.get("stock_code"))
            row = by_code.get(code)
            if row is None:
                continue
            active.append(self._member_row(row, member))

        master_count = len(members)
        active_count = len(active)
        coverage_pct = (
            round(active_count / master_count * 100.0, 2) if master_count else 0.0
        )
        rates = [item["change_rate"] for item in active if item["change_rate"] is not None]
        advancers = sum(1 for value in rates if value > 0)
        decliners = sum(1 for value in rates if value < 0)
        flat = sum(1 for value in rates if value == 0)
        breadth_pct = (
            round(advancers / len(rates) * 100.0, 2) if rates else 0.0
        )

        def weighted_total(key: str) -> float | None:
            values = [
                (item.get(key), float(item.get("master_weight") or 1.0))
                for item in active
                if item.get(key) is not None
            ]
            if not values:
                return None
            return round(sum(float(value) * weight for value, weight in values), 4)

        cumulative = weighted_total("trade_value_eok")
        one_min = weighted_total("trade_value_1m_eok")
        five_min = weighted_total("trade_value_5m_eok")
        program_net = weighted_total("program_net")
        large_net = weighted_total("large_trade_net_sum_eok")
        large_count = weighted_total("large_trade_net_count")
        avg_rate = _weighted_average(
            [
                (item.get("change_rate"), float(item.get("master_weight") or 1.0))
                for item in active
            ]
        )
        avg_candidate = _weighted_average(
            [
                (item.get("candidate_score"), float(item.get("master_weight") or 1.0))
                for item in active
            ]
        )
        max_candidate_values = [
            item["candidate_score"]
            for item in active
            if item["candidate_score"] is not None
        ]
        acceleration = (
            round(one_min / (five_min / 5.0), 4)
            if one_min is not None and five_min is not None and five_min > 0
            else None
        )

        contribution_key = (
            "trade_value_1m_eok"
            if one_min is not None and one_min > 0
            else "trade_value_eok"
        )
        contribution_values = [
            (
                item,
                max(0.0, float(item.get(contribution_key) or 0.0))
                * float(item.get("master_weight") or 1.0),
            )
            for item in active
        ]
        contribution_total = sum(value for _item, value in contribution_values)
        for item, value in contribution_values:
            contribution_pct = (
                value / contribution_total * 100.0 if contribution_total > 0 else 0.0
            )
            item["contribution_pct"] = round(contribution_pct, 2)
            item["contribution_text"] = _fmt_pct(contribution_pct, 1)

        concentration_values = sorted(
            (value for _item, value in contribution_values), reverse=True
        )
        top1_concentration = (
            concentration_values[0] / contribution_total * 100.0
            if contribution_values and contribution_total > 0
            else 0.0
        )
        top3_concentration = (
            sum(concentration_values[:3]) / contribution_total * 100.0
            if contribution_total > 0
            else 0.0
        )

        active.sort(
            key=lambda item: (
                -float(item.get("candidate_score") or 0.0),
                -float(item.get("leadership_score") or 0.0),
                -float(item.get("trade_value_eok") or 0.0),
                str(item.get("stock_code") or ""),
            )
        )
        for index, item in enumerate(active):
            if index == 0:
                role = "주도"
                role_class = "lead"
            elif item.get("leadership_score", 0) >= 65:
                role = "동반"
                role_class = "co"
            elif (item.get("change_rate") or 0) > 0:
                role = "후발"
                role_class = "follow"
            else:
                role = "관찰"
                role_class = "watch"
            item["leadership_role"] = role
            item["role_class"] = role_class
            item["display_rank"] = index + 1

        min_active = max(1, int(theme.get("min_active_members") or 1))
        coverage_status = (
            "READY"
            if coverage_pct >= 80 and active_count >= min_active
            else "PARTIAL"
            if coverage_pct >= 60
            else "LOW_COVERAGE"
            if coverage_pct >= 40
            else "WAIT_DATA"
        )
        leaders = [
            {
                key: item.get(key)
                for key in (
                    "stock_code",
                    "stock_name",
                    "change_rate",
                    "change_rate_text",
                    "change_rate_tone",
                    "trade_value_eok",
                    "trade_value_text",
                    "candidate_score",
                    "candidate_score_text",
                    "candidate_grade",
                    "leadership_score",
                    "leadership_role",
                )
            }
            for item in active[:3]
        ]

        return {
            "theme_id": theme["theme_id"],
            "theme_name": theme["theme_name"],
            "short_name": theme.get("short_name") or theme["theme_name"],
            "min_active_members": min_active,
            "member_count": master_count,
            "master_member_count": master_count,
            "active_member_count": active_count,
            "coverage_pct": coverage_pct,
            "coverage_text": _fmt_pct(coverage_pct, 0),
            "coverage_status": coverage_status,
            "advancers": advancers,
            "decliners": decliners,
            "flat": flat,
            "breadth_pct": breadth_pct,
            "breadth_text": f"상승 {advancers}/{len(rates)}",
            "avg_change_rate": round(avg_rate, 4) if avg_rate is not None else None,
            "weighted_change_rate": round(avg_rate, 4) if avg_rate is not None else None,
            "max_change_rate": round(max(rates), 4) if rates else None,
            "change_rate_text": _fmt_pct(avg_rate, 2),
            "change_rate_tone": _tone(avg_rate),
            "trade_value_eok": cumulative or 0.0,
            "trade_value_acc_eok": cumulative or 0.0,
            "trade_value_text": _fmt_eok(cumulative),
            "trade_value_1m_eok": one_min,
            "trade_value_1m_text": _fmt_eok(one_min),
            "trade_value_1m_tone": _tone(one_min),
            "trade_value_5m_eok": five_min,
            "trade_value_5m_text": _fmt_eok(five_min),
            "trade_value_5m_tone": _tone(five_min),
            "acceleration": acceleration,
            "acceleration_text": _fmt_number(acceleration, 2),
            "program_net_eok": program_net,
            "program_net_text": _fmt_eok(program_net),
            "large_trade_net_eok": large_net,
            "large_trade_net_count": large_count,
            "large_trade_text": _fmt_eok(large_net),
            "avg_candidate_score": (
                round(avg_candidate, 4) if avg_candidate is not None else None
            ),
            "max_candidate_score": (
                round(max(max_candidate_values), 4)
                if max_candidate_values
                else None
            ),
            "top1_concentration_pct": round(top1_concentration, 2),
            "top3_concentration_pct": round(top3_concentration, 2),
            "concentration_text": _fmt_pct(top3_concentration, 0),
            "top_stock_code": leaders[0]["stock_code"] if leaders else "",
            "top_stock_name": leaders[0]["stock_name"] if leaders else None,
            "top_stock_change_rate": (
                leaders[0]["change_rate"] if leaders else None
            ),
            "top_stock_candidate_score": (
                leaders[0]["candidate_score"] if leaders else None
            ),
            "leaders": leaders,
            "members": active,
        }

    def _score_themes(self, themes: list[dict[str, Any]]) -> None:
        one_rank = _rank_percent(
            {
                theme["theme_id"]: float(theme["trade_value_1m_eok"])
                for theme in themes
                if theme.get("trade_value_1m_eok") is not None
            }
        )
        five_rank = _rank_percent(
            {
                theme["theme_id"]: float(theme["trade_value_5m_eok"])
                for theme in themes
                if theme.get("trade_value_5m_eok") is not None
            }
        )
        cumulative_rank = _rank_percent(
            {
                theme["theme_id"]: float(theme.get("trade_value_eok") or 0.0)
                for theme in themes
            }
        )

        max_one = max(
            (float(theme.get("trade_value_1m_eok") or 0.0) for theme in themes),
            default=0.0,
        )
        max_five = max(
            (float(theme.get("trade_value_5m_eok") or 0.0) for theme in themes),
            default=0.0,
        )

        for theme in themes:
            theme_id = theme["theme_id"]
            components: list[tuple[float, float]] = []
            if theme_id in one_rank:
                components.append((one_rank[theme_id], 25.0))
            if theme_id in five_rank:
                components.append((five_rank[theme_id], 20.0))
            components.append((cumulative_rank.get(theme_id, 0.0), 15.0))
            if theme.get("avg_candidate_score") is not None:
                components.append(
                    (_clamp(float(theme["avg_candidate_score"])), 20.0)
                )
            if theme.get("active_member_count"):
                components.append((_clamp(theme.get("breadth_pct") or 0.0), 10.0))
            if (
                theme.get("program_net_eok") is not None
                or theme.get("large_trade_net_eok") is not None
            ):
                program = float(theme.get("program_net_eok") or 0.0)
                large = float(theme.get("large_trade_net_eok") or 0.0)
                components.append((_clamp(50.0 + program * 2.0 + large * 10.0), 10.0))

            active_weight = sum(weight for _value, weight in components)
            raw_score = (
                sum(value * weight for value, weight in components) / active_weight
                if active_weight > 0
                else 0.0
            )
            coverage = float(theme.get("coverage_pct") or 0.0)
            if coverage < 40:
                raw_score = min(raw_score, 59.0)
            elif coverage < 60:
                raw_score = min(raw_score, 69.0)
            elif coverage < 80:
                raw_score = min(raw_score, 79.0)
            score = round(_clamp(raw_score), 2)
            grade = _grade(score)
            metric_coverage_pct = round(active_weight, 2)

            acceleration = theme.get("acceleration")
            if theme.get("coverage_status") == "WAIT_DATA":
                state = "WAIT_DATA"
            elif score >= 85 and (
                (acceleration is not None and acceleration >= 1.15)
                or one_rank.get(theme_id, 0.0) >= 80
            ):
                state = "SURGE"
            elif score >= 70:
                state = "RISING"
            elif acceleration is not None and acceleration < 0.7:
                state = "COOLING"
            else:
                state = "STEADY"

            theme.update(
                {
                    "score": score,
                    "score_text": f"{score:.1f}",
                    "grade": grade,
                    "grade_class": f"grade-{grade.lower()}",
                    "state": state,
                    "status": state,
                    "state_text": state,
                    "state_class": state.lower().replace("_", "-"),
                    "metric_coverage_pct": metric_coverage_pct,
                    "metric_coverage_text": _fmt_pct(metric_coverage_pct, 0),
                    "trade_value_1m_bar_pct": (
                        round(float(theme.get("trade_value_1m_eok") or 0.0) / max_one * 100.0, 2)
                        if max_one > 0
                        else 0.0
                    ),
                    "trade_value_5m_bar_pct": (
                        round(float(theme.get("trade_value_5m_eok") or 0.0) / max_five * 100.0, 2)
                        if max_five > 0
                        else 0.0
                    ),
                }
            )

    def __call__(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        _meta: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        themes, mapping_status = self.loader.load()
        by_code = {
            _code(row.get("stock_code")): row
            for row in rows
            if _code(row.get("stock_code"))
        }
        if not themes:
            return {
                "schema_version": 2,
                "source": "theme_projection_engine",
                "status": "WAIT_THEME_MAP",
                "ts": now_text(),
                "input_feature_version": feature_version,
                "theme_count": 0,
                "row_count": 0,
                "rows": [],
                "details": {},
                "mapping": mapping_status,
                "calculate_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "policy": {
                    "direct_tr_allowed": False,
                    "candidate_rescore_allowed": False,
                    "html_calculation_allowed": False,
                    "input": "board_data_hub_shared_feature_snapshot",
                },
            }

        result = [
            self._aggregate_theme(theme, by_code)
            for theme in themes
        ]
        result = [theme for theme in result if theme.get("active_member_count", 0) > 0]
        self._score_themes(result)
        result.sort(
            key=lambda row: (
                -float(row.get("score") or 0.0),
                -float(row.get("trade_value_1m_eok") or 0.0),
                -float(row.get("trade_value_eok") or 0.0),
                str(row.get("theme_name") or ""),
            )
        )

        details: dict[str, dict[str, Any]] = {}
        compact: list[dict[str, Any]] = []
        for rank, theme in enumerate(result, start=1):
            theme["rank"] = rank
            theme["display_rank"] = rank
            detail = dict(theme)
            details[theme["theme_id"]] = detail
            summary = dict(theme)
            summary.pop("members", None)
            compact.append(summary)

        return {
            "schema_version": 2,
            "source": "theme_projection_engine",
            "status": "READY",
            "ts": now_text(),
            "input_feature_version": feature_version,
            "theme_count": len(compact),
            "row_count": len(compact),
            "rows": compact,
            "details": details,
            "mapping": mapping_status,
            "calculate_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "policy": {
                "direct_tr_allowed": False,
                "candidate_rescore_allowed": False,
                "html_calculation_allowed": False,
                "input": "board_data_hub_shared_feature_snapshot",
                "projection_interval_ms": 1000,
                "focus_badge_allowed": False,
            },
        }


class ThemeProjectionRuntime:
    def __init__(self, hub: BoardDataHub, min_interval_ms: int = 1000) -> None:
        self.worker = LatestOnlyProjectionWorker(
            name="theme",
            hub=hub,
            builder=ThemeProjectionBuilder(),
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
