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
    score_sum = 0.0
    weight_sum = 0.0
    for value, weight in components:
        if value is None or weight <= 0:
            continue
        score_sum += max(0.0, min(100.0, float(value))) * float(weight)
        weight_sum += float(weight)
    if weight_sum <= 0:
        return 0.0, 0.0
    return round(score_sum / weight_sum, 2), round(weight_sum, 2)


def _minmax_rank(
    records: list[dict[str, Any]],
    key: str,
    *,
    positive_only: bool = False,
    minimum: float | None = None,
) -> dict[str, float]:
    values: list[tuple[str, float]] = []
    for record in records:
        value = _number(record.get(key))
        if value is None:
            continue
        if positive_only and value <= 0:
            continue
        if minimum is not None and value < minimum:
            continue
        values.append((str(record.get("stock_code") or ""), float(value)))
    if not values:
        return {}
    low = min(value for _code_value, value in values)
    high = max(value for _code_value, value in values)
    if high <= low:
        return {stock_code: 100.0 for stock_code, _value in values}
    scale = 100.0 / (high - low)
    return {
        stock_code: round((value - low) * scale, 4)
        for stock_code, value in values
    }


def install(theme_module) -> None:
    """Select Theme leaders from price strength, persistence and relative money.

    One compact stock metric record is extracted for each shared Feature row and reused
    across every overlapping Theme. Theme membership is still visited once to assemble
    each Theme's small record list, but repeated raw-row parsing and repeated history
    scans are removed. Missing metrics are dynamically reweighted.
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
        self._theme_leader_member_codes_by_theme: dict[str, tuple[str, ...]] = {}

    def history_delta(
        self,
        stock_code: str,
        now_ts: float,
        seconds: int,
    ) -> float | None:
        history = self._theme_leader_history_by_code.get(stock_code)
        if not history:
            return None
        target = now_ts - float(seconds)
        for point_ts, point_value in reversed(history):
            if point_ts <= target:
                return round(float(history[-1][1]) - float(point_value), 6)
        return None

    def member_codes(
        self,
        theme_id: str,
        theme: dict[str, Any],
    ) -> tuple[str, ...]:
        raw_members = theme.get("members") or []
        codes = tuple(
            stock_code
            for member in raw_members
            if isinstance(member, dict)
            and (stock_code := _code(member.get("stock_code")))
        )
        cached = self._theme_leader_member_codes_by_theme.get(theme_id)
        if cached != codes:
            self._theme_leader_member_codes_by_theme[theme_id] = codes
        return codes

    def extract_stock_metrics(
        self,
        by_code: dict[str, dict[str, Any]],
        now_ts: float,
    ) -> tuple[
        dict[str, dict[str, Any]],
        dict[str, float | None],
        dict[str, float | None],
    ]:
        one_delta_by_code = {
            stock_code: history_delta(self, stock_code, now_ts, 60)
            for stock_code in by_code
        }
        five_delta_by_code = {
            stock_code: history_delta(self, stock_code, now_ts, 300)
            for stock_code in by_code
        }
        metrics: dict[str, dict[str, Any]] = {}
        for stock_code, row in by_code.items():
            groups = _blocked(row)
            metrics[stock_code] = {
                "stock_code": stock_code,
                "stock_name": row.get("stock_name") or stock_code,
                "row": row,
                "change_rate": _number(row.get("change_rate")),
                "stock_change_momentum_1m": one_delta_by_code.get(stock_code),
                "stock_change_persistence_5m": five_delta_by_code.get(stock_code),
                "amount_ratio": theme_module._first_number(
                    row, "amount_ratio", "trade_value_ratio"
                ),
                "trade_value_1m_eok": theme_module._first_number(
                    row, "trade_value_1m_eok", "one_min_trade_value_eok"
                ),
                "trade_value_5m_eok": theme_module._first_number(
                    row, "trade_value_5m_eok", "five_min_trade_value_eok"
                ),
                "trade_value_eok": _number(row.get("trade_value_eok")),
                "execution_strength": (
                    None
                    if "execution" in groups
                    else _number(row.get("execution_strength"))
                ),
                "strength_5m": (
                    None
                    if "strength5" in groups
                    else _number(row.get("strength_5m"))
                ),
                "program_net": (
                    None if "program" in groups else _number(row.get("program_net"))
                ),
                "large_trade_net_sum_eok": (
                    None
                    if "large_trade" in groups
                    else _number(row.get("large_trade_net_sum_eok"))
                ),
                "blocked_groups": sorted(groups),
            }
        return metrics, one_delta_by_code, five_delta_by_code

    def score_records(
        records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        change_rank = _minmax_rank(records, "change_rate")
        one_change_rank = _minmax_rank(
            records, "stock_change_momentum_1m", positive_only=True
        )
        five_change_rank = _minmax_rank(
            records, "stock_change_persistence_5m", positive_only=True
        )
        amount_rank = _minmax_rank(records, "amount_ratio", positive_only=True)
        one_money_rank = _minmax_rank(
            records, "trade_value_1m_eok", positive_only=True
        )
        five_money_rank = _minmax_rank(
            records, "trade_value_5m_eok", positive_only=True
        )
        execution_rank = _minmax_rank(
            records, "execution_strength", minimum=100.0
        )
        strength5_rank = _minmax_rank(records, "strength_5m", minimum=100.0)
        program_rank = _minmax_rank(records, "program_net", positive_only=True)
        large_rank = _minmax_rank(
            records, "large_trade_net_sum_eok", positive_only=True
        )

        scored_by_code: dict[str, dict[str, Any]] = {}
        for record in records:
            stock_code = str(record.get("stock_code") or "")
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
                score = min(score, 39.0)
            record.update(
                {
                    "leadership_score": round(score, 2),
                    "leadership_score_text": f"{score:.1f}",
                    "leadership_metric_weight": active_weight,
                    "leadership_components": components,
                    "leadership_basis": (
                        "price_momentum_amount_flow_precomputed_minmax_v2"
                    ),
                }
            )
            scored_by_code[stock_code] = record

        records.sort(
            key=lambda record: (
                -int((_number(record.get("change_rate")) or 0.0) > 0),
                -float(record.get("leadership_score") or 0.0),
                -float(record.get("change_rate") or -999.0),
                -float(record.get("stock_change_momentum_1m") or -999.0),
                -float(record.get("amount_ratio") or 0.0),
                -float(record.get("trade_value_1m_eok") or 0.0),
                str(record.get("stock_code") or ""),
            )
        )

        positive_leader_assigned = False
        for index, record in enumerate(records):
            rate = _number(record.get("change_rate")) or 0.0
            score = float(record.get("leadership_score") or 0.0)
            if not positive_leader_assigned and rate > 0:
                role, role_class = "주도", "lead"
                positive_leader_assigned = True
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
            one = record.get("stock_change_momentum_1m")
            five = record.get("stock_change_persistence_5m")
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
                    "stock_change_momentum_1m": one,
                    "stock_change_momentum_1m_text": (
                        "-" if one is None else f"{float(one):+.2f}%p"
                    ),
                    "stock_change_persistence_5m": five,
                    "stock_change_persistence_5m_text": (
                        "-" if five is None else f"{float(five):+.2f}%p"
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
                self._theme_leader_member_codes_by_theme.clear()
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

            stock_metrics, _one_delta, _five_delta = extract_stock_metrics(
                self, by_code, now_ts
            )
            leader_maps: dict[str, dict[str, dict[str, Any]]] = {}
            for summary in summaries:
                if not isinstance(summary, dict):
                    continue
                theme_id = str(summary.get("theme_id") or "")
                theme = mapping_by_id.get(theme_id)
                if not isinstance(theme, dict):
                    continue
                records = [
                    dict(stock_metrics[stock_code])
                    for stock_code in member_codes(self, theme_id, theme)
                    if stock_code in stock_metrics
                ]
                leaders, scored_by_code = score_records(records)
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
            "stock_metric_extract_passes": 1,
            "per_theme_raw_metric_reparse": False,
            "rank_method": "within_theme_minmax",
            "positive_stock_precedence": True,
        }
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["theme_leader_selection"] = (
                "price30_momentum25_amount20_recent_money15_strength_flow10"
            )
            policy["theme_leader_candidate_score_used"] = False
            policy["theme_leader_dynamic_reweight"] = True
            policy["theme_leader_additional_tr_allowed"] = False
            policy["theme_leader_positive_stock_precedence"] = True
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
            detail_policy["positive_stock_precedence"] = True
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class.build_selected_detail = build_selected_detail
    builder_class._stockboard_theme_leader_selection_installed = True
