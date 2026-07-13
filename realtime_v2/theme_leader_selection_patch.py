from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


HISTORY_WINDOW_SEC = 310.0
TOP_PER_VIEW = 10

# source key, payload component key, weight, positive-only, minimum
_METRIC_SPECS = (
    ("change_rate", "change_rate_rank", 30.0, False, None),
    ("stock_change_momentum_1m", "change_momentum_1m_rank", 15.0, True, None),
    ("stock_change_persistence_5m", "change_persistence_5m_rank", 10.0, True, None),
    ("amount_ratio", "amount_ratio_rank", 20.0, True, None),
    ("trade_value_1m_eok", "trade_value_1m_rank", 9.0, True, None),
    ("trade_value_5m_eok", "trade_value_5m_rank", 6.0, True, None),
    ("execution_strength", "execution_strength_rank", 3.0, False, 100.0),
    ("strength_5m", "strength_5m_rank", 2.0, False, 100.0),
    ("program_net", "program_net_rank", 2.5, True, None),
    ("large_trade_net_sum_eok", "large_trade_rank", 2.5, True, None),
)


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


def _eligible(
    value: float | None,
    *,
    positive_only: bool,
    minimum: float | None,
) -> bool:
    if value is None:
        return False
    if positive_only and value <= 0:
        return False
    if minimum is not None and value < minimum:
        return False
    return True


def install(theme_module) -> None:
    """Select precise leaders only for top Theme views and selected detail.

    All Theme summary rows keep a lightweight fallback leader from the summary pass.
    Precise price/momentum/money/strength/flow scoring is limited to the union of the
    top momentum and money views. Any selected Theme is always scored precisely by the
    independent detail worker. No TR/OpenAPI work or browser-side calculation is added.
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

    def history_deltas(
        self,
        stock_code: str,
        now_ts: float,
    ) -> tuple[float | None, float | None]:
        history = self._theme_leader_history_by_code.get(stock_code)
        if not history:
            return None, None
        latest = float(history[-1][1])
        target_one = now_ts - 60.0
        target_five = now_ts - 300.0
        one = None
        five = None
        for point_ts, point_value in reversed(history):
            if one is None and point_ts <= target_one:
                one = round(latest - float(point_value), 6)
            if point_ts <= target_five:
                five = round(latest - float(point_value), 6)
                break
        return one, five

    def member_codes(
        self,
        theme_id: str,
        theme: dict[str, Any],
    ) -> tuple[str, ...]:
        cached = self._theme_leader_member_codes_by_theme.get(theme_id)
        if cached is not None:
            return cached
        raw_members = theme.get("members") or []
        codes = tuple(
            stock_code
            for member in raw_members
            if isinstance(member, dict)
            and (stock_code := _code(member.get("stock_code")))
        )
        self._theme_leader_member_codes_by_theme[theme_id] = codes
        return codes

    def update_history(
        self,
        by_code: dict[str, dict[str, Any]],
        now_ts: float,
        hold_active: bool,
    ) -> None:
        if hold_active:
            return
        cutoff = now_ts - HISTORY_WINDOW_SEC
        for stock_code, row in by_code.items():
            rate = _number(row.get("change_rate"))
            if rate is None:
                continue
            history = self._theme_leader_history_by_code.setdefault(stock_code, deque())
            if history and now_ts - history[-1][0] < 0.9:
                history[-1] = (now_ts, rate)
            else:
                history.append((now_ts, rate))
            while history and history[0][0] < cutoff:
                history.popleft()

    def extract_stock_metrics(
        self,
        by_code: dict[str, dict[str, Any]],
        now_ts: float,
        needed_codes: set[str],
    ) -> dict[str, dict[str, Any]]:
        metrics: dict[str, dict[str, Any]] = {}
        for stock_code in needed_codes:
            row = by_code.get(stock_code)
            if row is None:
                continue
            one_delta, five_delta = history_deltas(self, stock_code, now_ts)
            groups = _blocked(row)
            metrics[stock_code] = {
                "stock_code": stock_code,
                "stock_name": row.get("stock_name") or stock_code,
                "row": row,
                "change_rate": _number(row.get("change_rate")),
                "stock_change_momentum_1m": one_delta,
                "stock_change_persistence_5m": five_delta,
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
        return metrics

    def score_records(
        base_records: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        metric_count = len(_METRIC_SPECS)
        lows = [float("inf")] * metric_count
        highs = [float("-inf")] * metric_count
        counts = [0] * metric_count

        # Pass 1: all metric ranges together.
        for record in base_records:
            for index, (key, _component, _weight, positive_only, minimum) in enumerate(
                _METRIC_SPECS
            ):
                value = _number(record.get(key))
                if not _eligible(
                    value,
                    positive_only=positive_only,
                    minimum=minimum,
                ):
                    continue
                numeric = float(value)
                if numeric < lows[index]:
                    lows[index] = numeric
                if numeric > highs[index]:
                    highs[index] = numeric
                counts[index] += 1

        scored_records: list[dict[str, Any]] = []
        scored_by_code: dict[str, dict[str, Any]] = {}

        # Pass 2: normalize and score directly, without rank dictionaries.
        for base_record in base_records:
            components: dict[str, float | None] = {}
            score_sum = 0.0
            weight_sum = 0.0
            for index, (key, component, weight, positive_only, minimum) in enumerate(
                _METRIC_SPECS
            ):
                value = _number(base_record.get(key))
                if counts[index] <= 0 or not _eligible(
                    value,
                    positive_only=positive_only,
                    minimum=minimum,
                ):
                    normalized = None
                elif highs[index] <= lows[index]:
                    normalized = 100.0
                else:
                    normalized = round(
                        (float(value) - lows[index])
                        * 100.0
                        / (highs[index] - lows[index]),
                        4,
                    )
                components[component] = normalized
                if normalized is not None:
                    score_sum += normalized * weight
                    weight_sum += weight

            score = score_sum / weight_sum if weight_sum > 0 else 0.0
            rate = _number(base_record.get("change_rate"))
            if rate is None or rate <= 0:
                score = min(score, 39.0)

            record = dict(base_record)
            record.update(
                {
                    "leadership_score": round(score, 2),
                    "leadership_score_text": f"{score:.1f}",
                    "leadership_metric_weight": round(weight_sum, 2),
                    "leadership_components": components,
                    "leadership_basis": (
                        "price_momentum_amount_flow_two_pass_minmax_v4"
                    ),
                }
            )
            scored_records.append(record)
            scored_by_code[str(record.get("stock_code") or "")] = record

        scored_records.sort(
            key=lambda record: (
                -int((_number(record.get("change_rate")) or 0.0) > 0),
                -float(record.get("leadership_score") or 0.0),
                -float(
                    _number(record.get("change_rate"))
                    if _number(record.get("change_rate")) is not None
                    else -999.0
                ),
                -float(
                    _number(record.get("stock_change_momentum_1m"))
                    if _number(record.get("stock_change_momentum_1m")) is not None
                    else -999.0
                ),
                -float(_number(record.get("amount_ratio")) or 0.0),
                -float(_number(record.get("trade_value_1m_eok")) or 0.0),
                str(record.get("stock_code") or ""),
            )
        )

        positive_leader_assigned = False
        for index, record in enumerate(scored_records):
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
        for record in scored_records[:3]:
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

    def top_scope_ids(payload: dict[str, Any]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for source in (
            payload.get("rows") if isinstance(payload.get("rows"), list) else [],
            payload.get("money_rows")
            if isinstance(payload.get("money_rows"), list)
            else [],
        ):
            for row in source[:TOP_PER_VIEW]:
                if not isinstance(row, dict):
                    continue
                theme_id = str(row.get("theme_id") or "")
                if not theme_id or theme_id in seen:
                    continue
                seen.add(theme_id)
                selected.append(theme_id)
        return selected

    def sanitize_fallback(summary: dict[str, Any]) -> None:
        leaders = summary.get("leaders")
        if not isinstance(leaders, list):
            summary["leader_precision"] = "summary_fallback"
            return
        valid = [dict(row) for row in leaders if isinstance(row, dict)]
        valid.sort(
            key=lambda row: (
                -int((_number(row.get("change_rate")) or 0.0) > 0),
                -float(_number(row.get("leadership_score")) or 0.0),
                -float(_number(row.get("change_rate")) or -999.0),
                str(row.get("stock_code") or ""),
            )
        )
        positive_assigned = False
        for index, row in enumerate(valid):
            rate = _number(row.get("change_rate")) or 0.0
            score = float(_number(row.get("leadership_score")) or 0.0)
            if not positive_assigned and rate > 0:
                role, role_class = "주도", "lead"
                positive_assigned = True
            elif rate > 0 and score >= 65:
                role, role_class = "동반", "co"
            elif rate > 0:
                role, role_class = "후발", "follow"
            else:
                role, role_class = "관찰", "watch"
            row["leadership_role"] = role
            row["role_class"] = role_class
            row["leadership_rank"] = index + 1
        summary["leaders"] = valid[:3]
        summary["leader_precision"] = "summary_fallback"
        summary["top_stock_code"] = valid[0].get("stock_code") if valid else ""
        summary["top_stock_name"] = valid[0].get("stock_name") if valid else None
        summary["top_stock_change_rate"] = (
            valid[0].get("change_rate") if valid else None
        )

    def apply_precise_summary(
        summary: dict[str, Any],
        leaders: list[dict[str, Any]],
    ) -> None:
        summary["leaders"] = leaders
        summary["leader_precision"] = "precise_top_union"
        summary["top_stock_code"] = leaders[0].get("stock_code") if leaders else ""
        summary["top_stock_name"] = leaders[0].get("stock_name") if leaders else None
        summary["top_stock_change_rate"] = (
            leaders[0].get("change_rate") if leaders else None
        )
        summary["top_stock_leadership_score"] = (
            leaders[0].get("leadership_score") if leaders else None
        )

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
            by_code = self._theme_latest_flow_rows_by_code
            mapping_by_id = self._theme_latest_mapping_by_id

        precise_ids = top_scope_ids(payload)
        precise_id_set = set(precise_ids)

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

            update_history(self, by_code, now_ts, hold_active)

            needed_codes: set[str] = set()
            for theme_id in precise_ids:
                theme = mapping_by_id.get(theme_id)
                if isinstance(theme, dict):
                    needed_codes.update(member_codes(self, theme_id, theme))
            stock_metrics = extract_stock_metrics(self, by_code, now_ts, needed_codes)

            summary_by_id = {
                str(row.get("theme_id") or ""): row
                for row in summaries
                if isinstance(row, dict)
            }
            leader_maps: dict[str, dict[str, dict[str, Any]]] = {}
            for theme_id, summary in summary_by_id.items():
                if theme_id not in precise_id_set:
                    sanitize_fallback(summary)
                    continue
                theme = mapping_by_id.get(theme_id)
                if not isinstance(theme, dict):
                    sanitize_fallback(summary)
                    continue
                base_records = [
                    stock_metrics[stock_code]
                    for stock_code in member_codes(self, theme_id, theme)
                    if stock_code in stock_metrics
                ]
                leaders, scored_by_code = score_records(base_records)
                leader_maps[theme_id] = scored_by_code
                apply_precise_summary(summary, leaders)

            money_rows = payload.get("money_rows")
            if isinstance(money_rows, list):
                for money_row in money_rows:
                    if not isinstance(money_row, dict):
                        continue
                    source = summary_by_id.get(str(money_row.get("theme_id") or ""))
                    if source is None:
                        continue
                    for key in (
                        "leaders",
                        "leader_precision",
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
        payload["calculate_ms"] = round(total_ms, 3)
        payload["leader_selection_status"] = {
            "enabled": True,
            "theme_count": len(summaries),
            "precise_theme_count": len(precise_id_set),
            "fallback_theme_count": max(0, len(summaries) - len(precise_id_set)),
            "top_per_view": TOP_PER_VIEW,
            "precise_member_count": len(needed_codes),
            "tracked_stock_count": len(self._theme_leader_history_by_code),
            "history_window_sec": int(HISTORY_WINDOW_SEC),
            "hold_active": hold_active,
            "trading_date": self._theme_leader_basis_date or trading_date or None,
            "candidate_score_used": False,
            "dynamic_reweight": True,
            "additional_tr_allowed": False,
            "summary_extra_member_passes": 1,
            "summary_member_scope": "top_momentum_money_union_only",
            "stock_metric_extract_passes": 1,
            "per_theme_raw_metric_reparse": False,
            "rank_method": "top_union_two_pass_minmax",
            "rank_metric_passes_per_theme": 2,
            "positive_stock_precedence": True,
            "selected_detail_always_precise": True,
        }
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["theme_leader_selection"] = (
                "top10_each_view_price30_momentum25_amount20_recent_money15_strength_flow10"
            )
            policy["theme_leader_candidate_score_used"] = False
            policy["theme_leader_dynamic_reweight"] = True
            policy["theme_leader_additional_tr_allowed"] = False
            policy["theme_leader_positive_stock_precedence"] = True
            policy["theme_leader_precision_scope"] = (
                "top_momentum_money_union_plus_selected_detail"
            )
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
        theme_payload = payload.get("theme")
        members = (
            theme_payload.get("members") if isinstance(theme_payload, dict) else None
        )
        if not isinstance(members, list):
            return payload

        selected = str(theme_id or "")
        now_ts = _number((meta or {}).get("snapshot_epoch")) or time.time()
        with self._theme_summary_split_lock:
            mapping = self._theme_latest_mapping_by_id.get(selected)
            by_code = self._theme_latest_flow_rows_by_code

        precise_leaders: list[dict[str, Any]] = []
        score_by_code: dict[str, dict[str, Any]] = {}
        with self._theme_leader_lock:
            if isinstance(mapping, dict):
                selected_codes = set(member_codes(self, selected, mapping))
                stock_metrics = extract_stock_metrics(
                    self, by_code, now_ts, selected_codes
                )
                base_records = [
                    stock_metrics[stock_code]
                    for stock_code in member_codes(self, selected, mapping)
                    if stock_code in stock_metrics
                ]
                precise_leaders, score_by_code = score_records(base_records)
                self._theme_leader_scores_by_theme[selected] = score_by_code

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
        theme_payload["members"] = members
        theme_payload["leaders"] = precise_leaders
        theme_payload["leader_precision"] = "selected_detail_precise"

        detail_ms = (time.perf_counter() - started) * 1000.0
        payload["calculate_ms"] = round(detail_ms, 3)
        payload["leader_detail_status"] = {
            "selected_theme_id": selected,
            "precise": True,
            "member_count": len(score_by_code),
            "calculate_ms": round(detail_ms, 3),
        }
        detail_policy = payload.setdefault("policy", {})
        if isinstance(detail_policy, dict):
            detail_policy["leader_selection"] = (
                "selected_theme_precise_price_momentum_amount_money_strength_flow"
            )
            detail_policy["candidate_score_used_for_leader"] = False
            detail_policy["browser_leader_sort_allowed"] = False
            detail_policy["positive_stock_precedence"] = True
            detail_policy["selected_detail_always_precise"] = True
        return payload

    builder_class.__init__ = init
    builder_class.__call__ = call
    builder_class.build_selected_detail = build_selected_detail
    builder_class._stockboard_theme_leader_selection_installed = True
