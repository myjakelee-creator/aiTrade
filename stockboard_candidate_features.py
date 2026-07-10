"""Feature extraction for validated StockBoard candidate models."""
from __future__ import annotations
from dataclasses import dataclass
from statistics import median
from typing import Any

from stockboard_candidate_config import (
    _age_sec, _clamp, _current_rank, _first, _five_min_strength,
    _number_or_none, _previous_trade_value, _program_net,
    _rank_position_score, _rank_rise_score, _rank_value_scores,
    _realtime_strength, _round_score, _row_ohlc_value,
    _strength_score, _trade_value,
)

@dataclass(frozen=True)
class FeatureResult:
    key: str
    points: float
    value: float | None
    source: str
    status: str = "ok"

    def item(self, *, label: str, weight: float) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": label,
            "points": _round_score(self.points),
            "weight": weight,
            "weighted_points": round(self.points * weight / 100.0, 4),
            "possible_points": max(weight, 0.0),
            "source": self.source,
            "status": self.status,
            "value": self.value,
        }


class FeatureSnapshot:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows
        self.total = len(rows)
        self.amount_ratio_values: dict[int, float] = {}
        self.trade_values: dict[int, float] = {}
        self.program_ratio_values: dict[int, float] = {}
        self.large_ratio_values: dict[int, float] = {}
        self.relative_values: dict[int, float] = {}
        rates = [_number_or_none(row.get("change_rate")) for row in rows]
        valid_rates = sorted(value for value in rates if value is not None)
        trimmed = valid_rates
        if len(valid_rates) >= 20:
            cut = max(1, int(len(valid_rates) * 0.05))
            trimmed = valid_rates[cut:-cut] or valid_rates
        self.board_median_rate = float(median(trimmed)) if trimmed else 0.0
        for index, row in enumerate(rows):
            trade = _trade_value(row)
            previous = _previous_trade_value(row)
            if trade is not None and trade > 0:
                self.trade_values[index] = trade
                if previous is not None and previous > 0:
                    self.amount_ratio_values[index] = trade / previous
            program = _program_net(row)
            if trade is not None and trade > 0 and program is not None and program > 0:
                self.program_ratio_values[index] = program / trade
            large_net = _number_or_none(row.get("large_trade_net_sum_eok"))
            if trade is not None and trade > 0 and large_net is not None and large_net > 0:
                self.large_ratio_values[index] = large_net / trade
            rate = _number_or_none(row.get("change_rate"))
            if rate is not None:
                self.relative_values[index] = rate - self.board_median_rate
        self.amount_scores = _rank_value_scores(self.amount_ratio_values, self.total)
        self.trade_scores = _rank_value_scores(self.trade_values, self.total)
        self.program_scores = _rank_value_scores(self.program_ratio_values, self.total)
        self.large_ratio_scores = _rank_value_scores(self.large_ratio_values, self.total)
        self.relative_scores = _rank_value_scores(self.relative_values, self.total)
        self._cache: dict[tuple[int, str], FeatureResult] = {}

    def get(self, index: int, key: str) -> FeatureResult:
        cache_key = (index, key)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._calculate(index, key)
        return self._cache[cache_key]

    def _calculate(self, index: int, key: str) -> FeatureResult:
        row = self.rows[index]
        if key == "trade_value_rank":
            rank = _current_rank(row)
            return FeatureResult(key, _rank_position_score(int(rank), self.total) if rank else 0, rank, "rank", "ok" if rank else "missing")
        if key == "rank_gap":
            points, value, status = _rank_rise_score(row)
            return FeatureResult(key, points, value, "prev_rank/current_rank", status)
        if key == "amount_ratio":
            value = self.amount_ratio_values.get(index)
            return FeatureResult(key, self.amount_scores.get(index, 0), value, "trade_value_eok/prev_trade_value_eok", "ok" if value is not None else "missing")
        if key == "trade_value_amount":
            value = self.trade_values.get(index)
            return FeatureResult(key, self.trade_scores.get(index, 0), value, "trade_value_eok", "ok" if value is not None else "missing")
        if key == "instant_strength":
            value = _realtime_strength(row)
            if value is None:
                return FeatureResult(key, 0, None, "execution_strength", "missing")
            age = _age_sec(_first(row, "execution_strength_updated_at", "received_at"))
            status = "stale" if age is not None and age > 10 else "ok"
            return FeatureResult(key, 0 if status == "stale" else _strength_score(value), value, "execution_strength", status)
        if key == "five_min_strength":
            value = _five_min_strength(row)
            return FeatureResult(key, _strength_score(value), value, "strength_5m", "ok" if value is not None and value > 0 else "missing")
        if key == "program_net":
            value = _program_net(row)
            if value is None:
                return FeatureResult(key, 0, None, "program_net/trade_value_eok", "missing")
            age = _age_sec(row.get("program_net_updated_at"))
            if age is not None and age > 120:
                return FeatureResult(key, 0, value, "program_net/trade_value_eok", "stale")
            return FeatureResult(key, self.program_scores.get(index, 0) if value > 0 else 0, value, "program_net/trade_value_eok", "ok")
        if key == "large_trade":
            values = [_number_or_none(row.get(name)) for name in (
                "large_trade_buy_count", "large_trade_sell_count", "large_trade_buy_sum_eok",
                "large_trade_sell_sum_eok", "large_trade_net_sum_eok",
            )]
            if all(value is None for value in values):
                return FeatureResult(key, 0, None, "large_trade_aggregate", "missing")
            buy_count, sell_count, buy_sum, sell_sum, net_sum = [value or 0.0 for value in values]
            net_sum = values[4] if values[4] is not None else buy_sum - sell_sum
            if net_sum <= 0:
                return FeatureResult(key, 0, net_sum, "large_trade_aggregate", "ok")
            amount_total, count_total = buy_sum + sell_sum, buy_count + sell_count
            amount_share = buy_sum / amount_total * 100 if amount_total > 0 else 0
            count_share = buy_count / count_total * 100 if count_total > 0 else 0
            score = self.large_ratio_scores.get(index, 0) * .60 + amount_share * .25 + count_share * .15
            cap = 0 if count_total <= 0 else 60 if count_total <= 2 else 80 if count_total <= 4 else 100
            return FeatureResult(key, min(_round_score(score), cap), net_sum, "large_trade_net_ratio+buy_share", "ok")
        if key in {"above_open", "candle_location", "near_high", "upper_wick_ratio", "reversal_penalty"}:
            price = _number_or_none(_first(row, "price", "trade_price", "day_close"))
            open_price = _row_ohlc_value(row, "open")
            high = _row_ohlc_value(row, "high")
            low = _row_ohlc_value(row, "low")
            if price is None or open_price is None:
                return FeatureResult(key, 0, None, "ohlc", "missing")
            if key == "above_open":
                return FeatureResult(key, 100 if price >= open_price else 0, 1 if price >= open_price else 0, "price>=open", "ok")
            if high is None or low is None:
                return FeatureResult(key, 0, None, "ohlc", "missing")
            spread = high - low
            location = 50.0 if spread <= 0 else _clamp((price - low) / spread * 100)
            upper_wick = 0.0 if spread <= 0 else _clamp((high - max(open_price, price)) / spread * 100)
            high_drop = 0.0 if high <= 0 else max(0.0, (high - price) / high * 100)
            if key in {"candle_location", "near_high"}:
                return FeatureResult(key, location, location, "(price-low)/(high-low)", "ok")
            if key == "upper_wick_ratio":
                return FeatureResult(key, 100 - upper_wick, upper_wick, "upper_wick_ratio", "ok")
            penalty = _clamp(max(0.0, upper_wick - 20) * 2 + high_drop * 12)
            return FeatureResult(key, penalty, penalty, "upper_wick+high_drop", "ok")
        if key == "not_overheated":
            rate = _number_or_none(row.get("change_rate"))
            if rate is None:
                return FeatureResult(key, 0, None, "change_rate", "missing")
            score = 0 if rate < -2 else 30 if rate < 0 else 100 if rate <= 3 else 80 if rate <= 7 else 40 if rate <= 10 else 0
            return FeatureResult(key, score, rate, "change_rate_band", "ok")
        if key == "relative_change":
            value = self.relative_values.get(index)
            return FeatureResult(key, self.relative_scores.get(index, 0), value, "change_rate-board_median", "ok" if value is not None else "missing")
        if key == "green_while_board_weak":
            rate = _number_or_none(row.get("change_rate"))
            if rate is None:
                return FeatureResult(key, 0, None, "change_rate+board_median", "missing")
            hit = self.board_median_rate < 0 and rate > 0
            return FeatureResult(key, 100 if hit else 0, 1 if hit else 0, "green_while_board_weak", "ok")
        if key in {"strength_alignment", "strength_divergence_penalty"}:
            instant, five = _realtime_strength(row), _five_min_strength(row)
            if instant is None or five is None:
                return FeatureResult(key, 0, None, "execution_strength+strength_5m", "missing")
            if key == "strength_alignment":
                return FeatureResult(key, min(_strength_score(instant), _strength_score(five)), min(instant, five), "strength_alignment", "ok")
            penalty = 100 if instant >= 120 and five < 90 else 80 if instant - five >= 60 else 40 if instant - five >= 35 else 0
            return FeatureResult(key, penalty, instant - five, "instant-five_divergence", "ok")
        if key in {"program_plus_strength", "flow_confirmation", "combination_quality"}:
            program = self.get(index, "program_net")
            large = self.get(index, "large_trade")
            instant = self.get(index, "instant_strength")
            five = self.get(index, "five_min_strength")
            rank_gap = self.get(index, "rank_gap")
            amount = self.get(index, "amount_ratio")
            if key == "program_plus_strength":
                strength = max(instant.points, five.points)
                return FeatureResult(key, _round_score((program.points + strength) / 2), None, "program+strength", "ok")
            if key == "flow_confirmation":
                return FeatureResult(key, max(program.points, large.points), None, "program_or_large", "ok")
            money = 100 if rank_gap.points >= 70 and amount.points >= 70 else 60 if rank_gap.points >= 60 and amount.points >= 60 else 30 if max(rank_gap.points, amount.points) >= 80 else 0
            flow = max(program.points, large.points)
            execution = max(instant.points, five.points)
            dual = 100 if program.points >= 60 and large.points >= 60 else 60 if flow >= 80 else 30 if flow >= 60 else 0
            strong_count = len([score for score in (rank_gap.points, amount.points, execution, program.points, large.points) if score >= 60])
            count_score = 100 if strong_count == 5 else 80 if strong_count == 4 else 40 if strong_count == 3 else 0
            score = money * .32 + (100 if execution >= 60 and flow >= 70 else 60 if execution >= 60 and flow >= 50 else 20 if execution >= 60 else 0) * .28 + dual * .20 + count_score * .20
            return FeatureResult(key, _round_score(score), score, "five_factor_alignment", "ok")
        raise KeyError(f"unsupported feature: {key}")


