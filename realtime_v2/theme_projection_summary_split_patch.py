from __future__ import annotations

import threading
import time
from statistics import median
from typing import Any

from realtime_v2.common import now_text


def install(theme_module) -> None:
    """Split all-theme summary from selected-theme detail.

    The summary path visits raw shared rows and emits only theme-level scalars plus
    three compact leader records. Full member display rows are built only through
    ``build_selected_detail`` for one selected theme.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_summary_split_installed", False):
        return

    original_init = builder_class.__init__

    def number(value: Any) -> float | None:
        return theme_module._number(value)

    def code(value: Any) -> str:
        return theme_module._code(value)

    def blocked(row: dict[str, Any]) -> set[str]:
        raw = row.get("metric_scoring_blocked_groups")
        if isinstance(raw, (list, tuple, set)):
            return {str(value or "").strip().lower() for value in raw if value}
        return set()

    def leadership(row: dict[str, Any], blocked_groups: set[str]) -> float:
        score = 0.0
        weight = 0.0
        candidate = theme_module._first_number(row, "candidate_score", "grade_score")
        rate = number(row.get("change_rate"))
        ratio = theme_module._first_number(row, "amount_ratio", "trade_value_ratio")
        execution = (
            None
            if "execution" in blocked_groups
            else number(row.get("execution_strength"))
        )
        program = (
            None if "program" in blocked_groups else number(row.get("program_net"))
        )
        large = (
            None
            if "large_trade" in blocked_groups
            else number(row.get("large_trade_net_sum_eok"))
        )
        if candidate is not None:
            score += theme_module._clamp(candidate) * 45.0
            weight += 45.0
        if rate is not None:
            score += theme_module._clamp((rate + 5.0) * 10.0) * 20.0
            weight += 20.0
        if ratio is not None:
            score += theme_module._clamp(ratio * 20.0) * 15.0
            weight += 15.0
        if execution is not None:
            score += theme_module._clamp((execution - 70.0) / 1.3) * 10.0
            weight += 10.0
        if program is not None or large is not None:
            flow = 0.0
            divisor = 0
            if program is not None:
                flow += theme_module._clamp(50.0 + program * 2.0)
                divisor += 1
            if large is not None:
                flow += theme_module._clamp(50.0 + large * 10.0)
                divisor += 1
            score += (flow / max(1, divisor)) * 10.0
            weight += 10.0
        return round(score / weight if weight else 0.0, 2)

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._theme_summary_split_lock = threading.RLock()
        self._theme_latest_flow_rows_by_code: dict[str, dict[str, Any]] = {}
        self._theme_latest_summary_by_id: dict[str, dict[str, Any]] = {}
        self._theme_latest_mapping_by_id: dict[str, dict[str, Any]] = {}

    def aggregate_summary(
        self,
        theme: dict[str, Any],
        by_code: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        members = theme.get("members") or []
        master_count = len(members)
        active_count = 0
        rates: list[float] = []
        ratios: list[float] = []
        candidate_pairs: list[tuple[float, float]] = []
        cumulative = one_total = five_total = 0.0
        cumulative_found = one_found = five_found = False
        display_program = display_large = display_large_count = 0.0
        display_program_found = display_large_found = display_large_count_found = False
        score_program = score_large = 0.0
        score_program_found = score_large_found = False
        contributions: list[float] = []
        leader_rows: list[tuple[float, float, float, str, dict[str, Any]]] = []
        advancers = decliners = flat = held_count = 0

        for member in members:
            if not isinstance(member, dict):
                continue
            stock_code = code(member.get("stock_code"))
            row = by_code.get(stock_code)
            if row is None:
                continue
            active_count += 1
            weight = number(member.get("weight")) or 1.0
            if weight <= 0:
                continue
            groups = blocked(row)
            held_count += int(bool(groups))

            rate = number(row.get("change_rate"))
            if rate is not None:
                rates.append(rate)
                if rate > 0:
                    advancers += 1
                elif rate < 0:
                    decliners += 1
                else:
                    flat += 1

            ratio = theme_module._first_number(
                row, "amount_ratio", "trade_value_ratio"
            )
            if ratio is not None and ratio > 0:
                ratios.append(ratio)

            trade_value = number(row.get("trade_value_eok"))
            one = theme_module._first_number(
                row, "trade_value_1m_eok", "one_min_trade_value_eok"
            )
            five = theme_module._first_number(
                row, "trade_value_5m_eok", "five_min_trade_value_eok"
            )
            if trade_value is not None:
                cumulative += trade_value * weight
                cumulative_found = True
            if one is not None:
                one_total += one * weight
                one_found = True
            if five is not None:
                five_total += five * weight
                five_found = True

            candidate = theme_module._first_number(
                row, "candidate_score", "grade_score"
            )
            if candidate is not None:
                candidate_pairs.append((candidate, weight))

            program = number(row.get("program_net"))
            if program is not None:
                display_program += program * weight
                display_program_found = True
                if "program" not in groups:
                    score_program += program * weight
                    score_program_found = True

            large = number(row.get("large_trade_net_sum_eok"))
            if large is not None:
                display_large += large * weight
                display_large_found = True
                if "large_trade" not in groups:
                    score_large += large * weight
                    score_large_found = True

            large_count = number(row.get("large_trade_net_count"))
            if large_count is not None:
                display_large_count += large_count * weight
                display_large_count_found = True

            contributions.append(
                max(0.0, float(one if one is not None else trade_value or 0.0))
                * weight
            )
            leader_score = leadership(row, groups)
            leader_rows.append(
                (
                    float(candidate or 0.0),
                    leader_score,
                    float(trade_value or 0.0),
                    stock_code,
                    {
                        "stock_code": stock_code,
                        "stock_name": row.get("stock_name") or stock_code,
                        "change_rate": rate,
                        "change_rate_text": theme_module._fmt_pct(rate, 2),
                        "change_rate_tone": theme_module._tone(rate),
                        "trade_value_eok": trade_value,
                        "trade_value_text": theme_module._fmt_eok(trade_value),
                        "candidate_score": candidate,
                        "candidate_score_text": theme_module._fmt_number(candidate, 1),
                        "candidate_grade": str(
                            row.get("candidate_grade")
                            or row.get("grade")
                            or (
                                theme_module._grade(candidate)
                                if candidate is not None
                                else "-"
                            )
                        ),
                        "leadership_score": leader_score,
                    },
                )
            )

        coverage = (
            round(active_count / master_count * 100.0, 2) if master_count else 0.0
        )
        rate_count = len(rates)
        breadth = (
            round(advancers / rate_count * 100.0, 2) if rate_count else 0.0
        )
        avg_rate = sum(rates) / rate_count if rate_count else None
        median_rate = float(median(rates)) if rates else None
        amount_ratio = float(median(ratios)) if ratios else None
        amount_coverage = (
            round(len(ratios) / active_count * 100.0, 2) if active_count else 0.0
        )
        candidate_weight = sum(weight for _value, weight in candidate_pairs)
        avg_candidate = (
            sum(value * weight for value, weight in candidate_pairs)
            / candidate_weight
            if candidate_weight
            else None
        )
        cumulative_value = round(cumulative, 4) if cumulative_found else None
        one_value = round(one_total, 4) if one_found else None
        five_value = round(five_total, 4) if five_found else None
        acceleration = (
            round(one_value / (five_value / 5.0), 4)
            if one_value is not None and five_value is not None and five_value > 0
            else None
        )

        contribution_total = sum(contributions)
        ordered_contributions = sorted(contributions, reverse=True)
        top1 = (
            ordered_contributions[0] / contribution_total * 100.0
            if ordered_contributions and contribution_total > 0
            else 0.0
        )
        top3 = (
            sum(ordered_contributions[:3]) / contribution_total * 100.0
            if contribution_total > 0
            else 0.0
        )

        leader_rows.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        leaders: list[dict[str, Any]] = []
        for index, (_candidate, leader_score, _trade, _code, leader) in enumerate(
            leader_rows[:3]
        ):
            if index == 0:
                role = "주도"
            elif leader_score >= 65:
                role = "동반"
            elif (leader.get("change_rate") or 0) > 0:
                role = "후발"
            else:
                role = "관찰"
            leader["leadership_role"] = role
            leaders.append(leader)

        min_active = max(
            1, int(number(theme.get("min_active_members")) or 1)
        )
        coverage_status = (
            "READY"
            if coverage >= 80 and active_count >= min_active
            else "PARTIAL"
            if coverage >= 60
            else "LOW_COVERAGE"
            if coverage >= 40
            else "WAIT_DATA"
        )
        return {
            "theme_id": str(theme.get("theme_id") or ""),
            "theme_name": theme.get("theme_name"),
            "short_name": theme.get("short_name") or theme.get("theme_name"),
            "min_active_members": min_active,
            "member_count": master_count,
            "master_member_count": master_count,
            "active_member_count": active_count,
            "coverage_pct": coverage,
            "coverage_text": theme_module._fmt_pct(coverage, 0),
            "coverage_status": coverage_status,
            "advancers": advancers,
            "decliners": decliners,
            "flat": flat,
            "breadth_pct": breadth,
            "breadth_text": f"상승 {advancers}/{rate_count}",
            "avg_change_rate": round(avg_rate, 4) if avg_rate is not None else None,
            "weighted_change_rate": (
                round(avg_rate, 4) if avg_rate is not None else None
            ),
            "median_change_rate": (
                round(median_rate, 4) if median_rate is not None else None
            ),
            "median_change_rate_text": (
                "-" if median_rate is None else f"{median_rate:+.2f}%"
            ),
            "max_change_rate": round(max(rates), 4) if rates else None,
            "change_rate_text": theme_module._fmt_pct(avg_rate, 2),
            "change_rate_tone": theme_module._tone(avg_rate),
            "theme_amount_ratio": (
                round(amount_ratio, 4) if amount_ratio is not None else None
            ),
            "theme_amount_ratio_text": (
                "-" if amount_ratio is None else f"{amount_ratio:.2f}x"
            ),
            "amount_ratio_member_count": len(ratios),
            "amount_ratio_coverage_pct": amount_coverage,
            "amount_ratio_coverage_text": f"{amount_coverage:.0f}%",
            "trade_value_eok": cumulative_value or 0.0,
            "trade_value_acc_eok": cumulative_value or 0.0,
            "trade_value_text": theme_module._fmt_eok(cumulative_value),
            "trade_value_1m_eok": one_value,
            "trade_value_1m_text": theme_module._fmt_eok(one_value),
            "trade_value_1m_tone": theme_module._tone(one_value),
            "trade_value_5m_eok": five_value,
            "trade_value_5m_text": theme_module._fmt_eok(five_value),
            "trade_value_5m_tone": theme_module._tone(five_value),
            "acceleration": acceleration,
            "acceleration_text": theme_module._fmt_number(acceleration, 2),
            "program_net_eok": (
                round(display_program, 4) if display_program_found else None
            ),
            "program_net_text": theme_module._fmt_eok(
                round(display_program, 4) if display_program_found else None
            ),
            "large_trade_net_eok": (
                round(display_large, 4) if display_large_found else None
            ),
            "large_trade_net_count": (
                round(display_large_count, 4)
                if display_large_count_found
                else None
            ),
            "large_trade_text": theme_module._fmt_eok(
                round(display_large, 4) if display_large_found else None
            ),
            "_theme_score_program_net_eok": (
                round(score_program, 4) if score_program_found else None
            ),
            "_theme_score_large_trade_net_eok": (
                round(score_large, 4) if score_large_found else None
            ),
            "avg_candidate_score": (
                round(avg_candidate, 4) if avg_candidate is not None else None
            ),
            "top1_concentration_pct": round(top1, 2),
            "top3_concentration_pct": round(top3, 2),
            "concentration_text": theme_module._fmt_pct(top3, 0),
            "top_stock_code": leaders[0]["stock_code"] if leaders else "",
            "top_stock_name": leaders[0]["stock_name"] if leaders else None,
            "top_stock_change_rate": (
                leaders[0]["change_rate"] if leaders else None
            ),
            "top_stock_candidate_score": (
                leaders[0]["candidate_score"] if leaders else None
            ),
            "leaders": leaders,
            "held_member_count": held_count,
            "data_basis_text": (
                "전일 보존값 포함·점수 제외"
                if held_count
                else "현재/마감 보존값"
            ),
        }

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        _meta: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        themes, mapping_status = self.loader.load()
        by_code = {
            code(row.get("stock_code")): row
            for row in rows
            if code(row.get("stock_code"))
        }
        with self._theme_summary_split_lock:
            self._theme_latest_flow_rows_by_code = by_code
            self._theme_latest_mapping_by_id = {
                str(theme.get("theme_id") or ""): theme
                for theme in themes
                if isinstance(theme, dict)
            }

        if not themes:
            return {
                "schema_version": 3,
                "source": "theme_summary_projection",
                "status": "WAIT_THEME_MAP",
                "ts": now_text(),
                "input_feature_version": feature_version,
                "theme_count": 0,
                "row_count": 0,
                "rows": [],
                "mapping": mapping_status,
                "calculate_ms": round((time.perf_counter() - started) * 1000, 3),
            }

        aggregate_started = time.perf_counter()
        result = [aggregate_summary(self, theme, by_code) for theme in themes]
        result = [
            theme
            for theme in result
            if int(theme.get("active_member_count") or 0) > 0
        ]
        aggregate_ms = (time.perf_counter() - aggregate_started) * 1000.0

        score_started = time.perf_counter()
        self._score_themes(result)
        result.sort(
            key=lambda row: (
                -float(row.get("score") or 0.0),
                -float(row.get("trade_value_1m_eok") or 0.0),
                -float(row.get("trade_value_eok") or 0.0),
                str(row.get("theme_name") or ""),
            )
        )
        for rank, theme in enumerate(result, start=1):
            theme["rank"] = rank
            theme["display_rank"] = rank
        score_ms = (time.perf_counter() - score_started) * 1000.0

        with self._theme_summary_split_lock:
            self._theme_latest_summary_by_id = {
                str(row.get("theme_id") or ""): dict(row) for row in result
            }

        total_ms = (time.perf_counter() - started) * 1000.0
        return {
            "schema_version": 3,
            "source": "theme_summary_projection",
            "status": "READY",
            "ts": now_text(),
            "input_feature_version": feature_version,
            "theme_count": len(result),
            "row_count": len(result),
            "rows": result,
            "mapping": mapping_status,
            "calculate_ms": round(total_ms, 3),
            "performance_breakdown": {
                "summary_only": True,
                "total_ms": round(total_ms, 3),
                "aggregate_ms": round(aggregate_ms, 3),
                "score_sort_ms": round(score_ms, 3),
                "other_ms": round(max(0.0, total_ms - aggregate_ms - score_ms), 3),
                "full_theme_detail_rows_built": 0,
            },
            "policy": {
                "direct_tr_allowed": False,
                "candidate_rescore_allowed": False,
                "html_calculation_allowed": False,
                "input": "board_data_hub_shared_feature_snapshot",
                "projection_interval_ms": 1000,
                "summary_only": True,
                "all_theme_member_detail_generation_allowed": False,
                "selected_detail_projection": "theme_detail_latest_only_depth_1",
                "current_money_ranking_preserved_until_stage3": True,
            },
        }

    def build_selected_detail(
        self,
        feature_version: int,
        theme_id: str,
        rows: tuple[dict[str, Any], ...],
        _meta: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        selected = str(theme_id or "").strip()
        with self._theme_summary_split_lock:
            theme = self._theme_latest_mapping_by_id.get(selected)
            summary = self._theme_latest_summary_by_id.get(selected)
            flow_rows = dict(self._theme_latest_flow_rows_by_code)
        if not selected:
            status = "WAIT_SELECTION"
        elif theme is None:
            status = "WAIT_THEME_MAP"
        elif summary is None:
            status = "WAIT_THEME_SUMMARY"
        else:
            status = "READY"

        if status != "READY":
            return {
                "schema_version": 1,
                "source": "theme_selected_detail_projection",
                "status": status,
                "theme_id": selected,
                "input_feature_version": feature_version,
                "theme": None,
                "calculate_ms": round((time.perf_counter() - started) * 1000, 3),
            }

        raw_by_code = {
            code(row.get("stock_code")): row
            for row in rows
            if code(row.get("stock_code"))
        }
        selected_codes = {
            code(member.get("stock_code"))
            for member in theme.get("members") or []
            if isinstance(member, dict)
        }
        by_code: dict[str, dict[str, Any]] = {}
        for stock_code in selected_codes:
            raw = raw_by_code.get(stock_code)
            if raw is None:
                continue
            flow = flow_rows.get(stock_code)
            if flow is None:
                by_code[stock_code] = raw
                continue
            merged = dict(raw)
            for key in (
                "trade_value_1m_eok",
                "trade_value_5m_eok",
                "theme_flow_display_basis",
            ):
                if flow.get(key) is not None:
                    merged[key] = flow.get(key)
            by_code[stock_code] = merged

        detail = self._aggregate_theme(theme, by_code)
        detail.update(
            {
                key: value
                for key, value in summary.items()
                if key not in {"leaders"}
            }
        )
        detail["members"] = detail.get("members") or []
        detail.pop("_theme_score_program_net_eok", None)
        detail.pop("_theme_score_large_trade_net_eok", None)
        for member in detail["members"]:
            if not isinstance(member, dict):
                continue
            for key in (
                "_theme_score_program_net",
                "_theme_score_large_trade_net",
                "_theme_score_execution_strength",
            ):
                member.pop(key, None)

        total_ms = (time.perf_counter() - started) * 1000.0
        return {
            "schema_version": 1,
            "source": "theme_selected_detail_projection",
            "status": "READY",
            "ts": now_text(),
            "theme_id": selected,
            "input_feature_version": feature_version,
            "theme": detail,
            "calculate_ms": round(total_ms, 3),
            "policy": {
                "selected_theme_only": True,
                "direct_tr_allowed": False,
                "html_calculation_allowed": False,
                "queue": "latest_only_depth_1",
            },
        }

    builder_class.__init__ = init
    builder_class._aggregate_theme_summary = aggregate_summary
    builder_class.__call__ = call
    builder_class.build_selected_detail = build_selected_detail
    builder_class._stockboard_summary_split_installed = True
