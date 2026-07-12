from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from stockboard_theme_master import ThemeDefinition, ThemeMaster


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _rank_scores(values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    total = len(ordered)
    if total <= 1:
        return {key: 100.0 for key, _ in ordered}
    return {
        key: round((total - index - 1) / (total - 1) * 100.0, 4)
        for index, (key, _) in enumerate(ordered)
    }


def _strength_score(value: float | None) -> float:
    if value is None:
        return 0.0
    return _clamp((value - 60.0) / 100.0 * 100.0)


def _weighted_average(items: Iterable[tuple[float | None, float]]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for value, weight in items:
        if value is None or weight <= 0:
            continue
        numerator += value * weight
        denominator += weight
    return numerator / denominator if denominator > 0 else None


@dataclass
class HistoryPoint:
    ts: float
    value: float


@dataclass
class ThemeStateMemory:
    current: str = "WAIT_DATA"
    pending: str | None = None
    pending_count: int = 0


@dataclass
class ThemeBoardEngine:
    master: ThemeMaster
    history_seconds: int = 305
    history_by_code: dict[str, deque[HistoryPoint]] = field(default_factory=dict)
    last_trade_value_by_code: dict[str, float] = field(default_factory=dict)
    state_memory_by_theme: dict[str, ThemeStateMemory] = field(default_factory=dict)
    trading_date: str = ""

    def reset(self, trading_date: str = "") -> None:
        self.history_by_code.clear()
        self.last_trade_value_by_code.clear()
        self.state_memory_by_theme.clear()
        self.trading_date = str(trading_date or "")

    def _append_history(self, code: str, ts: float, value: float) -> None:
        history = self.history_by_code.setdefault(code, deque())
        last = self.last_trade_value_by_code.get(code)
        if last is not None and value + 1e-9 < last:
            history.clear()
        if history and int(history[-1].ts) == int(ts):
            history[-1] = HistoryPoint(ts=ts, value=value)
        else:
            history.append(HistoryPoint(ts=ts, value=value))
        cutoff = ts - max(self.history_seconds + 5, 310)
        while history and history[0].ts < cutoff:
            history.popleft()
        self.last_trade_value_by_code[code] = value

    def _delta(self, code: str, now_ts: float, seconds: int) -> float | None:
        history = self.history_by_code.get(code)
        if not history:
            return None
        target = now_ts - seconds
        current = history[-1].value
        candidate: HistoryPoint | None = None
        for point in reversed(history):
            if point.ts <= target:
                candidate = point
                break
        if candidate is None:
            return None
        return max(0.0, current - candidate.value)

    def update(
        self,
        rows: list[dict[str, Any]],
        *,
        now_ts: float | None = None,
        trading_date: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        now_ts = float(now_ts if now_ts is not None else time.time())
        trading_date = str(trading_date or "")
        if trading_date and self.trading_date and trading_date != self.trading_date:
            self.reset(trading_date)
        elif trading_date and not self.trading_date:
            self.trading_date = trading_date

        row_by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("stock_code") or "").strip()
            if len(code) != 6 or not code.isdigit():
                continue
            row_by_code[code] = row
            cumulative = _num(row.get("trade_value_eok"))
            if cumulative is not None and cumulative >= 0:
                self._append_history(code, now_ts, cumulative)

        membership_count = self.master.membership_count_by_code
        total_market_value = sum(
            _num(row.get("trade_value_eok")) or 0.0 for row in row_by_code.values()
        )
        raw_themes: list[dict[str, Any]] = []
        for theme in self.master.themes:
            raw_themes.append(
                self._aggregate_theme(
                    theme,
                    row_by_code,
                    membership_count,
                    total_market_value,
                    now_ts,
                )
            )

        self._apply_scores(raw_themes)
        raw_themes.sort(key=lambda item: (-float(item.get("score") or 0.0), item["theme_id"]))
        for rank, theme in enumerate(raw_themes, start=1):
            theme["rank"] = rank

        details: dict[str, dict[str, Any]] = {}
        compact: list[dict[str, Any]] = []
        for theme in raw_themes:
            detail = dict(theme)
            members = detail.pop("members", [])
            details[theme["theme_id"]] = {**detail, "members": members}
            compact.append(detail)

        return {
            "schema_version": 1,
            "source": "stockboard_theme_engine",
            "trading_date": trading_date,
            "calculated_at": now_ts,
            "calculate_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "theme_count": len(compact),
            "member_count": sum(len(theme.members) for theme in self.master.themes),
            "themes": compact,
            "details": details,
        }

    def _aggregate_theme(
        self,
        theme: ThemeDefinition,
        row_by_code: dict[str, dict[str, Any]],
        membership_count: dict[str, int],
        total_market_value: float,
        now_ts: float,
    ) -> dict[str, Any]:
        members: list[dict[str, Any]] = []
        active_rows: list[tuple[Any, dict[str, Any]]] = []
        adjusted_theme_total = 0.0
        for member in theme.members:
            row = row_by_code.get(member.stock_code)
            if row is None:
                continue
            active_rows.append((member, row))
            cumulative = _num(row.get("trade_value_eok")) or 0.0
            adjusted_theme_total += cumulative * member.share_weight / max(
                1, membership_count.get(member.stock_code, 1)
            )

        coverage = len(active_rows) / len(theme.members) if theme.members else 0.0
        up_count = flat_count = down_count = 0
        one_values: list[tuple[str, float]] = []
        five_values: list[tuple[str, float]] = []
        cumulative_values: list[tuple[str, float]] = []
        program_sum = 0.0
        large_sum = 0.0
        weighted_rate_items: list[tuple[float | None, float]] = []
        instant_items: list[tuple[float | None, float]] = []
        five_strength_items: list[tuple[float | None, float]] = []

        for member, row in active_rows:
            code = member.stock_code
            cumulative = max(0.0, _num(row.get("trade_value_eok")) or 0.0)
            one = self._delta(code, now_ts, 60)
            five = self._delta(code, now_ts, 300)
            if one is not None:
                one_values.append((code, one))
            if five is not None:
                five_values.append((code, five))
            cumulative_values.append((code, cumulative))

            rate = _num(row.get("change_rate"))
            if rate is not None:
                if rate > 0:
                    up_count += 1
                elif rate < 0:
                    down_count += 1
                else:
                    flat_count += 1
            weight = one if one is not None and one > 0 else cumulative
            weighted_rate_items.append((rate, weight))
            instant_items.append((_num(row.get("execution_strength")), weight))
            five_strength_items.append((_num(row.get("strength_5m")), weight))
            program_sum += _num(row.get("program_net")) or 0.0
            large_sum += _num(row.get("large_trade_net_sum_eok")) or 0.0

        one_total = sum(value for _, value in one_values) if one_values else None
        five_total = sum(value for _, value in five_values) if five_values else None
        cumulative_total = sum(value for _, value in cumulative_values)
        concentration_source = one_values if one_values and (one_total or 0) > 0 else cumulative_values
        concentration_total = sum(value for _, value in concentration_source)
        sorted_contrib = sorted(concentration_source, key=lambda item: (-item[1], item[0]))
        top1_concentration = (
            sorted_contrib[0][1] / concentration_total * 100.0
            if sorted_contrib and concentration_total > 0
            else 0.0
        )
        top3_concentration = (
            sum(value for _, value in sorted_contrib[:3]) / concentration_total * 100.0
            if sorted_contrib and concentration_total > 0
            else 0.0
        )
        valid_rate_count = up_count + flat_count + down_count
        breadth = up_count / valid_rate_count * 100.0 if valid_rate_count else 0.0
        acceleration = (
            one_total / (five_total / 5.0)
            if one_total is not None and five_total is not None and five_total > 0
            else None
        )
        market_share = adjusted_theme_total / total_market_value * 100.0 if total_market_value > 0 else 0.0

        one_by_code = dict(one_values)
        five_by_code = dict(five_values)
        cumulative_by_code = dict(cumulative_values)
        member_rows: list[dict[str, Any]] = []
        for member, row in active_rows:
            code = member.stock_code
            contribution_base = one_by_code.get(code)
            contribution_denominator = one_total
            if contribution_base is None or not contribution_denominator:
                contribution_base = cumulative_by_code.get(code, 0.0)
                contribution_denominator = cumulative_total
            contribution_pct = (
                contribution_base / contribution_denominator * 100.0
                if contribution_denominator and contribution_denominator > 0
                else 0.0
            )
            member_rows.append(
                {
                    "stock_code": code,
                    "stock_name": row.get("stock_name") or code,
                    "master_role": member.role,
                    "price": _num(row.get("price")),
                    "change_rate": _num(row.get("change_rate")),
                    "trade_value_eok": _num(row.get("trade_value_eok")),
                    "trade_value_1m_eok": _round(one_by_code.get(code)),
                    "trade_value_5m_eok": _round(five_by_code.get(code)),
                    "contribution_pct": _round(contribution_pct, 2),
                    "execution_strength": _num(row.get("execution_strength")),
                    "strength_5m": _num(row.get("strength_5m")),
                    "program_net": _num(row.get("program_net")),
                    "large_trade_net_sum_eok": _num(row.get("large_trade_net_sum_eok")),
                    "large_trade_net_count": _num(row.get("large_trade_net_count")),
                    "ohlc": dict(row.get("ohlc")) if isinstance(row.get("ohlc"), dict) else None,
                    "one_min_ready": one_by_code.get(code) is not None,
                    "five_min_ready": five_by_code.get(code) is not None,
                }
            )

        return {
            "theme_id": theme.theme_id,
            "theme_name": theme.theme_name,
            "aliases": list(theme.aliases),
            "master_member_count": len(theme.members),
            "active_member_count": len(active_rows),
            "coverage": round(coverage, 4),
            "coverage_pct": round(coverage * 100.0, 2),
            "coverage_status": (
                "READY" if coverage >= 0.8 else
                "PARTIAL" if coverage >= 0.6 else
                "LOW_COVERAGE" if coverage >= 0.4 else
                "WAIT_DATA"
            ),
            "trade_value_acc_eok": round(cumulative_total, 4),
            "trade_value_1m_eok": _round(one_total),
            "trade_value_5m_eok": _round(five_total),
            "market_share_pct": round(market_share, 4),
            "up_count": up_count,
            "flat_count": flat_count,
            "down_count": down_count,
            "breadth_pct": round(breadth, 2),
            "weighted_change_rate": _round(_weighted_average(weighted_rate_items)),
            "instant_strength": _round(_weighted_average(instant_items)),
            "five_min_strength": _round(_weighted_average(five_strength_items)),
            "program_net_eok": round(program_sum, 4),
            "large_trade_net_eok": round(large_sum, 4),
            "top1_concentration_pct": round(top1_concentration, 2),
            "top3_concentration_pct": round(top3_concentration, 2),
            "acceleration": _round(acceleration),
            "one_min_ready_count": len(one_values),
            "five_min_ready_count": len(five_values),
            "members": member_rows,
        }

    def _apply_scores(self, themes: list[dict[str, Any]]) -> None:
        one_scores = _rank_scores({
            theme["theme_id"]: float(theme["trade_value_1m_eok"])
            for theme in themes
            if theme.get("trade_value_1m_eok") is not None
        })
        five_scores = _rank_scores({
            theme["theme_id"]: float(theme["trade_value_5m_eok"])
            for theme in themes
            if theme.get("trade_value_5m_eok") is not None
        })

        for theme in themes:
            theme_id = theme["theme_id"]
            one = one_scores.get(theme_id, 0.0)
            five = five_scores.get(theme_id, 0.0)
            acceleration = _num(theme.get("acceleration"))
            acceleration_score = 0.0 if acceleration is None else _clamp((acceleration - 0.5) / 1.5 * 100.0)
            breadth_score = _clamp(_num(theme.get("breadth_pct")) or 0.0)
            strength_score = min(
                _strength_score(_num(theme.get("instant_strength"))),
                _strength_score(_num(theme.get("five_min_strength"))),
            )
            flow_hits = int((_num(theme.get("program_net_eok")) or 0.0) > 0) + int(
                (_num(theme.get("large_trade_net_eok")) or 0.0) > 0
            )
            flow_score = 100.0 if flow_hits == 2 else 60.0 if flow_hits == 1 else 0.0
            persistence_score = 100.0 if (
                theme.get("trade_value_1m_eok") is not None
                and theme.get("trade_value_5m_eok") is not None
                and (acceleration or 0.0) >= 0.8
            ) else 40.0 if theme.get("trade_value_5m_eok") is not None else 0.0
            concentration = _num(theme.get("top1_concentration_pct")) or 0.0
            concentration_penalty = _clamp((concentration - 50.0) / 20.0 * 15.0, 0.0, 15.0)

            score = (
                one * 0.25
                + five * 0.20
                + acceleration_score * 0.15
                + breadth_score * 0.15
                + strength_score * 0.10
                + flow_score * 0.10
                + persistence_score * 0.05
                - concentration_penalty
            )
            coverage = _num(theme.get("coverage")) or 0.0
            warmup = theme.get("trade_value_1m_eok") is None
            if coverage < 0.4 or warmup:
                score = min(score, 49.0)
                target_status = "WAIT_DATA"
            else:
                if coverage < 0.6:
                    score = min(score, 69.0)
                elif coverage < 0.8:
                    score = min(score, 79.0)
                target_status = (
                    "SURGE" if score >= 85 and breadth_score >= 60 and one >= 90 else
                    "RISING" if score >= 70 else
                    "STEADY" if score >= 55 else
                    "COOLING"
                )
            status = self._advance_status(theme_id, target_status)
            theme.update(
                {
                    "score": round(_clamp(score), 2),
                    "status": status,
                    "target_status": target_status,
                    "score_items": {
                        "one_min_rank": round(one, 2),
                        "five_min_rank": round(five, 2),
                        "acceleration": round(acceleration_score, 2),
                        "breadth": round(breadth_score, 2),
                        "strength": round(strength_score, 2),
                        "flow": round(flow_score, 2),
                        "persistence": round(persistence_score, 2),
                        "concentration_penalty": round(concentration_penalty, 2),
                    },
                }
            )
            self._apply_member_leadership(theme)

    def _advance_status(self, theme_id: str, target: str) -> str:
        memory = self.state_memory_by_theme.setdefault(theme_id, ThemeStateMemory())
        if target == "WAIT_DATA":
            memory.current = target
            memory.pending = None
            memory.pending_count = 0
            return memory.current
        if memory.current == "WAIT_DATA":
            memory.current = target
            memory.pending = None
            memory.pending_count = 0
            return memory.current
        if target == memory.current:
            memory.pending = None
            memory.pending_count = 0
            return memory.current
        if memory.pending != target:
            memory.pending = target
            memory.pending_count = 1
        else:
            memory.pending_count += 1
        rank = {"COOLING": 0, "STEADY": 1, "RISING": 2, "SURGE": 3}
        required = 3 if rank.get(target, 0) > rank.get(memory.current, 0) else 5
        if memory.pending_count >= required:
            memory.current = target
            memory.pending = None
            memory.pending_count = 0
        return memory.current

    def _apply_member_leadership(self, theme: dict[str, Any]) -> None:
        members = theme.get("members") or []
        if not members:
            theme["leader_code"] = None
            theme["leader_name"] = None
            return
        sorted_by_trade = sorted(
            members,
            key=lambda item: (
                -float(item.get("trade_value_1m_eok") or item.get("trade_value_eok") or 0.0),
                item.get("stock_code") or "",
            ),
        )
        total = len(sorted_by_trade)
        for index, member in enumerate(sorted_by_trade):
            contribution = _clamp(_num(member.get("contribution_pct")) or 0.0)
            rank_score = 100.0 if total <= 1 else (total - index - 1) / (total - 1) * 100.0
            rate = _num(member.get("change_rate"))
            rate_score = 0.0 if rate is None else _clamp((rate + 2.0) / 12.0 * 100.0)
            strength = min(
                _strength_score(_num(member.get("execution_strength"))),
                _strength_score(_num(member.get("strength_5m"))),
            )
            program_score = 100.0 if (_num(member.get("program_net")) or 0.0) > 0 else 0.0
            large_score = 100.0 if (_num(member.get("large_trade_net_sum_eok")) or 0.0) > 0 else 0.0
            one_ready = 100.0 if member.get("one_min_ready") else 0.0
            score = (
                contribution * 0.30
                + rank_score * 0.20
                + rate_score * 0.15
                + strength * 0.15
                + program_score * 0.05
                + large_score * 0.10
                + one_ready * 0.05
            )
            if theme.get("coverage", 0.0) < 0.4:
                score = min(score, 64.0)
            member["leadership_score"] = round(_clamp(score), 2)
            member["leadership_role"] = (
                "주도" if score >= 90 else
                "동반" if score >= 80 else
                "후발" if score >= 65 else
                "관찰"
            )
        members.sort(key=lambda item: (-float(item.get("leadership_score") or 0.0), item.get("stock_code") or ""))
        theme["members"] = members
        theme["leader_code"] = members[0].get("stock_code")
        theme["leader_name"] = members[0].get("stock_name")
