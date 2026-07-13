from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


HISTORY_WINDOW_SEC = 310.0


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _code(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def _blocked(row: dict[str, Any]) -> set[str]:
    raw = row.get("metric_scoring_blocked_groups")
    if isinstance(raw, (list, tuple, set)):
        return {str(value or "").strip().lower() for value in raw if value}
    return set()


def _weighted_score(components: list[tuple[float | None, float]]) -> tuple[float, float]:
    usable = [
        (max(0.0, min(100.0, float(value))), float(weight))
        for value, weight in components
        if value is not None and float(weight) > 0
    ]
    total_weight = sum(weight for _value, weight in usable)
    if total_weight <= 0:
        return 0.0, 0.0
    score = sum(value * weight for value, weight in usable) / total_weight
    return round(score, 2), round(total_weight, 2)


def install(theme_module) -> None:
    """Select ThemeBoard leaders from price strength, persistence and relative money.

    The patch consumes only the already completed shared Theme summary input. It adds
    no TR/OpenAPI work and keeps one change-rate float per current-universe stock for
    about five minutes. Missing momentum fields are dynamically reweighted, so a
    reconnect or warm-up never blanks leader selection.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_theme_leader_selection_installed", False):
        return

    original_init = builder_class.__init__
    original_call = builder_class.__call__
    original_build_selected_detail = builder_class.build_selected_detail

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._theme_leader_lock = threading.RLock()
        self._theme_leader_history_by_code: dict[
            str, deque[tuple[float, float]]
        ] = {}
        self._theme_leader_basis_date = ""
        self._theme_leader_scores_by_theme: dict[
            str, dict[str, dict[str, Any]]
        ] = {}

    def delta(self, stock_code: str, now_ts: float, seconds: int) -> float | None:
        history = self._theme_leader_history_by_code.get(stock_code)
        if not history:
            return None
        target = now_ts - float(seconds)
        for point_ts, point_value in reversed(history):
            if point_ts <= target:
                return round(float(history[-1][1]) - float(point_value), 6)
        return None

    def rank_map(values: dict[str, float]) -> dict[str, float]:
        return theme_module._rank_percent(values)

    def score_theme(
        self,
        theme: dict[str, Any],
        by_code: dict[str, dict[str, Any]],
        now_ts: float,
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        for member in theme.get("members") or []:
            if not isinstance(member, dict):
                continue
            stock_code = _code(member.get("stock_code"))
            row = by_code.get(stock_code)
            if not stock_code or row is None:
                continue
            groups = _blocked(row)
            rate = _number(row.get("change_rate"))
            one_change = delta(self, stock_code, now_ts, 60)
            five_change = delta(self, stock_code, now_ts, 300)
            amount_ratio = theme_module._first_number(
                row, "amount_ratio", "trade_value_ratio"
            )
            one_money = theme_module._first_number(
                row, "trade_value_1m_eok", "one_min_trade_value_eok"
            )
            five_money = theme_module._first_number(
                row, "trade_value_5m_eok", "five_min_trade_value_eok"
            )
            execution = (
                None
                if "execution" in groups
                else _number(row.get("execution_strength"))
            )
            strength5 = (
                None if "strength5" in groups else _number(row.get("strength_5m"))
            )
            program = (
                None if "program" in groups else _number(row.get("program_net"))
            )
            large = (
                None
                if "large_trade" in groups
                else _number(row.get("large_trade_net_sum_eok"))
            )
            records.append(
                {
                    "stock_code": stock_code,
                    "stock_name": row.get("stock_name") or stock_code,
                    "row": row,
                    "change_rate": rate,
                    "stock_change_momentum_1m": one_change,
                    "stock_change_persistence_5m": five_change,
                    "amount_ratio": amount_ratio,
                    "trade_value_1m_eok": one_money,
                    "trade_value_5m_eok": five_money,
                    "trade_value_eok": _number(row.get("trade_value_eok")),
                    "execution_strength": execution,
                    "strength_5m": strength5,
                    "program_net": program,
                    "large_trade_net_sum_eok": large,
                    "blocked_groups": sorted(groups),
                }
            )

        def values(key: str, predicate=None) -> dict[str, float]:
            result: dict[str, float] = {}
            for record in records:
                value = _number(record.get(key))
                if value is None or (predicate is not None and not predicate(value)):
                    continue
                result[record["stock_code"]] = float(value)
            return result

        change_rank = rank_map(values("change_rate"))
        one_change_rank = rank_map(
            values("stock_change_momentum_1m", lambda value: value > 0)
        )
        five_change_rank = rank_map(
            values("stock_change_persistence_5m", lambda value: value > 0)
        )
        amount_rank = rank_map(values("amount_ratio", lambda value: value > 0))
        one_money_rank = rank_map(
            values("trade_value_1m_eok", lambda value: value > 0)
        )
        five_money_rank = rank_map(
            values("trade_value_5m_eok", lambda value: value > 0)
        )
        execution_rank = rank_map(
            values("execution_strength", lambda value: value >= 100)
        )
        strength5_rank = rank_map(values("strength_5m", lambda value: value >= 100))
        program_rank = rank_map(values("program_net", lambda value: value > 0))
        large_rank = rank_map(
            values("large_trade_net_sum_eok", lambda value: value > 0)
        )

        scored_by_code: dict[str, dict[str, Any]] = {}
        for record in records:
            stock_code = record["stock_code"]
            components = {
                "change_rate_rank": change_rank.get(stock_code),
                "change_momentum_1m_rank": one_change_rank.get(stock_code),
                "change_persistence_5m_rank": five_change_rank.get(stock_code),
                "amount_ratio_rank": amount_rank.get(stock_code),
                "trade_value_1m_rank": one_money_rank.get(stock_code),
                "trade_value_5m_rank": five_money_rank.get(stock_code),
                "execution_strength_rank": execution_rank.get(stock_code),
                "strength_5m_rank": strength5_rank.get(stock_code),
                "program_net_rank": program_rank.get(stock_code),
                "large_trade_rank": large_rank.get(stock_code),
            }
            score, active_weight = _weighted_score(
                [
                    (components["change_rate_rank"], 30.0),
                    (components["change_momentum_1m_rank"], 15.0),
                    (components["change_persistence_5m_rank"], 10.0),
                    (components["amount_ratio_rank"], 20.0),
                    (components["trade_value_1m_rank"], 9.0),
                    (components["trade_value_5m_rank"], 6.0),
                    (components["execution_strength_rank"], 3.0),
                    (components["strength_5m_rank"], 2.0),
                    (components["program_net_rank"], 2.5),
                    (components["large_trade_rank"], 2.5),
                ]
            )
            rate = _number(record.get("change_rate"))
            if rate is None or rate <= 0:
                score = min(score, 59.0)
            record.update(
                {
                    "leadership_score": round(score, 2),
                    "leadership_score_text": f"{score:.1f}",
                    "leadership_metric_weight": active_weight,
                    "leadership_components": components,
                    "leadership_basis": (
                        "price_momentum_amount_flow_dynamic_reweight_v1"
                    ),
                }
            )
            scored_by_code[stock_code] = record

        records.sort(
            key=lambda record: (
                -float(record.get("leadership_score") or 0.0),
                -float(record.get("change_rate") or -999.0),
                -float(record.get("stock_change_momentum_1m") or -999.0),
                -float(record.get("amount_ratio") or 0.0),
                -float(record.get("trade_value_1m_eok") or 0.0),
                str(record.get("stock_code") or ""),
            )
        )

        for index, record in enumerate(records):
            rate = _number(record.get("change_rate")) or 0.0
            score = float(record.get("leadership_score") or 0.0)
            if index == 0 and rate > 0:
                role, role_class = "주도", "lead"
            elif rate > 0 and score >= 65:
                role, role_class = "동반", "co"
            elif rate > 0:
                role, role_class = "후발", "follow"
            else:
                role, role_class = "관찰", "watch"
            record["leadership_role"] = role
            record["role_class"] = role_class
            record["leadership_rank"] = index + 1

        leaders: list[dict[str, Any]] = []
        for record in records[:3]:
            row = record.get("row") if isinstance(record.get("row"), dict) else {}
            leaders.append(
                {
                    "stock_code": record.get("stock_code"),
                    "stock_name": record.get("stock_name"),
                    "change_rate": record.get("change_rate"),
                    "change_rate_text": theme_module._fmt_pct(
                        record.get("change_rate"), 2
                    ),
                    "change_rate_tone": theme_module._tone(
                        record.get("change_rate")
                    ),
                    "trade_value_eok": record.get("trade_value_eok"),
                    "trade_value_text": theme_module._fmt_eok(
                        record.get("trade_value_eok")
                    ),
                    "trade_value_1m_eok": record.get("trade_value_1m_eok"),
                    "trade_value_1m_text": theme_module._fmt_eok(
                        record.get("trade_value_1m_eok")
                    ),
                    "trade_value_5m_eok": record.get("trade_value_5m_eok"),
                    "trade_value_5m_text": theme_module._fmt_eok(
                        record.get("trade_value_5m_eok")
                    ),
                    "amount_ratio": record.get("amount_ratio"),
                    "amount_ratio_text": theme_module._fmt_number(
                        record.get("amount_ratio"), 2
                    ),
                    "stock_change_momentum_1m": record.get(
                        "stock_change_momentum_1m"
                    ),
                    "stock_change_momentum_1m_text": (
                        "-"
                        if record.get("stock_change_momentum_1m") is None
                        else f"{float(record['stock_change_momentum_1m']):+.2f}%p"
                    ),
                    "stock_change_persistence_5m": record.get(
                        "stock_change_persistence_5m"
                    ),
                    "stock_change_persistence_5m_text": (
                        "-"
                        if record.get("stock_change_persistence_5m") is None
                        else f"{float(record['stock_change_persistence_5m']):+.2f}%p"
                    ),
                    "execution_strength": record.get("execution_strength"),
                    "strength_5m": record.get("strength_5m"),
                    "program_net": record.get("program_net"),
                    "large_trade_net_sum_eok": record.get(
                        "large_trade_net_sum_eok"
                    ),
                    "candidate_score": theme_module._first_number(
                        row, "candidate_score", "grade_score"
                    ),
                    "candidate_grade": str(
                        row.get("candidate_grade") or row.get("grade") or "-"
                    ),
                    "leadership_score": record.get("leadership_score"),
                    "leadership_score_text": record.get("leadership_score_text"),
                    "leadership_metric_weight": record.get(
                        "leadership_metric_weight"
                    ),
                    "leadership_role": record.get("leadership_role"),
                    "role_class": record.get("role_class"),
                    "leadership_rank": record.get("leadership_rank"),
                }
            )
        return leaders, scored_by_code

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
        trend_status = (
            payload.get("trend_feature_status")
            if isinstance(payload.get("trend_feature_status"), dict)
            else {}
        )
        trading_date = str(trend_status.get("trading_date") or "")
        hold_active = bool(trend_status.get("hold_active"))
        now_ts = _number((meta or {}).get("snapshot_epoch")) or time.time()

        with self._theme_summary_split_lock:
            by_code = dict(self._theme_latest_flow_rows_by_code)
            mapping_by_id = dict(self._theme_latest_mapping_by_id)

        cutoff = now_ts - HISTORY_WINDOW_SEC
        with self._theme_leader_lock:
            if (
                trading_date
                and self._theme_leader_basis_date
                and trading_date != self._theme_leader_basis_date
            ):
                self._theme_leader_history_by_code.clear()
                self._theme_leader_scores_by_theme.clear()
            if trading_date:
                self._theme_leader_basis_date = trading_date

            if not hold_active:
                for stock_code, row in by_code.items():
                    rate = _number(row.get("change_rate"))
                    if rate is None:
                        continue
                    history = self._theme_leader_history_by_code.setdefault(
                        stock_code, deque()
                    )
                    if history and now_ts - history[-1][0] < 0.9:
                        history[-1] = (now_ts, rate)
                    else:
                        history.append((now_ts, rate))
                    while history and history[0][0] < cutoff:
                        history.popleft()

            leader_maps: dict[str, dict[str, dict[str, Any]]] = {}
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                theme_id = str(summary.get("theme_id") or "")
                theme = mapping_by_id.get(theme_id)
                if not isinstance(theme, dict):
                    continue
                leaders, scored_by_code = score_theme(self, theme, by_code, now_ts)
                leader_maps[theme_id] = scored_by_code
                summary["leaders"] = leaders
                summary["top_stock_code"] = (
                    leaders[0].get("stock_code") if leaders else ""
                )
                summary["top_stock_name"] = (
                    leaders[0].get("stock_name") if leaders else None
                )
                summary["top_stock_change_rate"] = (
                    leaders[0].get("change_rate") if leaders else None
                )
                summary["top_stock_leadership_score"] = (
                    leaders[0].get("leadership_score") if leaders else None
                )

            money_rows = payload.get("money_rows")
            if isinstance(money_rows, list):
                summary_by_id = {
                    str(row.get("theme_id") or ""): row
                    for row in summaries
                    if isinstance(row, dict)
                }
                for money_row in money_rows:
                    if not isinstance(money_row, dict):
                        continue
                    source = summary_by_id.get(str(money_row.get("theme_id") or ""))
                    if source is None:
                        continue
                    for key in (
                        "leaders",
                        "top_stock_code",
                        "top_stock_name",
                        "top_stock_change_rate",
                        "top_stock_leadership_score",
                    ):
                        money_row[key] = source.get(key)

            self._theme_leader_scores_by_theme = leader_maps
            with self._theme_summary_split_lock:
                self._theme_latest_summary_by_id = {
                    str(row.get("theme_id") or ""): dict(row)
                    for row in summaries
                    if isinstance(row, dict)
                }

        leader_ms = (time.perf_counter() - started) * 1000.0
        total_ms = (time.perf_counter() - total_started) * 1000.0
        performance = payload.setdefault("performance_breakdown", {})
        if isinstance(performance, dict):
            performance["leader_rank_ms"] = round(leader_ms, 3)
            performance["total_ms"] = round(total_ms, 3)
            accounted = sum(
                float(performance.get(key) or 0.0)
                for key in (
                    "aggregate_ms",
                    "score_sort_ms",
                    "momentum_ms",
                    "dual_rank_ms",
                    "leader_rank_ms",
                )
            )
            performance["other_ms"] = round(max(0.0, total_ms - accounted), 3)
        payload["calculate_ms"] = round(total_ms, 3)
        payload["leader_selection_status"] = {
            "enabled": True,
            "theme_count": len(self._theme_leader_scores_by_theme),
            "tracked_stock_count": len(self._theme_leader_history_by_code),
            "history_window_sec": int(HISTORY_WINDOW_SEC),
            "hold_active": hold_active,
            "trading_date": self._theme_leader_basis_date or trading_date or None,
            "candidate_score_used": False,
            "dynamic_reweight": True,
            "additional_tr_allowed": False,
            "summary_extra_member_passes": 1,
        }
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["theme_leader_selection"] = (
                "price30_momentum25_amount20_recent_money15_strength_flow10"
            )
            policy["theme_leader_candidate_score_used"] = False
            policy["theme_leader_dynamic_reweight"] = True
            policy["theme_leader_additional_tr_allowed"] = False
        return payload

    def build_selected_detail(
        self,
        feature_version: int,
        theme_id: str,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        payload = original_build_selected_detail(
            self, feature_version, theme_id, rows, meta
        )
        if not isinstance(payload, dict) or payload.get("status") != "READY":
            return payload
        theme = payload.get("theme")
        members = theme.get("members") if isinstance(theme, dict) else None
        if not isinstance(members, list):
            return payload

        selected = str(theme_id or "")
        with self._theme_leader_lock:
            score_by_code = dict(
                self._theme_leader_scores_by_theme.get(selected) or {}
            )

        for member in members:
            if not isinstance(member, dict):
                continue
            stock_code = _code(member.get("stock_code"))
            score = score_by_code.get(stock_code)
            if score is None:
                continue
            for key in (
                "leadership_score",
                "leadership_score_text",
                "leadership_metric_weight",
                "leadership_components",
                "leadership_basis",
                "leadership_role",
                "role_class",
                "leadership_rank",
                "stock_change_momentum_1m",
                "stock_change_persistence_5m",
            ):
                member[key] = score.get(key)
            one = score.get("stock_change_momentum_1m")
            five = score.get("stock_change_persistence_5m")
            member["stock_change_momentum_1m_text"] = (
                "-" if one is None else f"{float(one):+.2f}%p"
            )
            member["stock_change_persistence_5m_text"] = (
                "-" if five is None else f"{float(five):+.2f}%p"
            )

        members.sort(
            key=lambda member: (
                int(member.get("leadership_rank") or 999999),
                -float(member.get("leadership_score") or 0.0),
                str(member.get("stock_code") or ""),
            )
        )
        theme["members"] = members
        theme["leaders"] = [
            {
                key: member.get(key)
                for key in (
                    "stock_code",
                    "stock_name",
                    "change_rate",
                    "change_rate_text",
                    "change_rate_tone",
                    "trade_value_eok",
                    "trade_value_text",
                    "leadership_score",
                    "leadership_score_text",
                    "leadership_role",
                    "role_class",
                    "leadership_rank",
                )
            }
            for member in members[:3]
        ]
        detail_ms = (time.perf_counter() - started) * 1000.0
        payload["calculate_ms"] = round(detail_ms, 3)
        detail_policy = payload.setdefault("policy", {})
        if isinstance(detail_policy, dict):
            detail_policy["leader_selection"] = (
                "server_price_momentum_amount_money_strength_flow"
            )
            detail_policy["candidate_score_used_for_leader"] = False
            detail_policy["browser_leader_sort_allowed"] = False
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class.build_selected_detail = build_selected_detail
    builder_class._stockboard_theme_leader_selection_installed = True
