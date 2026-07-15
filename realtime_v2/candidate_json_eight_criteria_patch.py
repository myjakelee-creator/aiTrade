from __future__ import annotations

from statistics import median
from typing import Any

CRITERIA_KEYS = (
    "trade_value_rank",
    "rank_gap",
    "amount_ratio",
    "instant_strength",
    "five_min_strength",
    "program_net",
    "large_trade",
    "combination_quality",
)
POLICY_TYPES = {
    "trade_value_rank": "linear_rank",
    "rank_gap": "rank_gap_composite",
    "amount_ratio": "positive_ratio_relative_rank",
    "instant_strength": "piecewise_linear",
    "five_min_strength": "piecewise_linear",
    "program_net": "positive_ratio_relative_rank",
    "large_trade": "large_trade_composite",
    "combination_quality": "combination_quality",
}
POLICY = "candidate_model_json_eight_criteria_on_demand_v1"


def _num(config, value: Any, default: float | None = None) -> float | None:
    result = config._number_or_none(value)
    return default if result is None else result


def _round(config, value: Any) -> float:
    return config._round_score(_num(config, value, 0.0))


def _policy(snapshot, key: str, expected: str) -> dict[str, Any]:
    policy = snapshot.feature_policies.get(key)
    if not isinstance(policy, dict):
        raise ValueError(f"{key} policy missing from final candidate JSON")
    actual = str(policy.get("type") or "")
    if actual != expected:
        raise ValueError(f"{key} policy type must be {expected}, got {actual}")
    return policy


def _linear_rank(config, rank: Any, policy: dict[str, Any]) -> float:
    value = _num(config, rank)
    if value is None:
        return _round(config, policy.get("missing_score", 0))
    start_rank = _num(config, policy.get("start_rank"))
    end_rank = _num(config, policy.get("end_rank"))
    start_score = _num(config, policy.get("start_score"))
    end_score = _num(config, policy.get("end_score"))
    if None in (start_rank, end_rank, start_score, end_score):
        raise ValueError("linear_rank policy incomplete")
    if value < start_rank or value > end_rank:
        return _round(config, policy.get("outside_score", 0))
    if start_rank == end_rank:
        return _round(config, start_score)
    ratio = (value - start_rank) / (end_rank - start_rank)
    return _round(config, start_score + ratio * (end_score - start_score))


def _piecewise(config, value: Any, policy: dict[str, Any]) -> float:
    number = _num(config, value)
    if number is None:
        return _round(config, policy.get("missing_score", 0))
    points: list[tuple[float, float]] = []
    for item in policy.get("points") or []:
        if not isinstance(item, dict):
            continue
        x = _num(config, item.get("value"))
        y = _num(config, item.get("score"))
        if x is not None and y is not None:
            points.append((x, y))
    points.sort()
    if not points:
        raise ValueError("piecewise policy has no points")
    if number <= points[0][0]:
        return _round(config, points[0][1])
    if number >= points[-1][0]:
        return _round(config, points[-1][1])
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if left_x <= number <= right_x:
            ratio = (number - left_x) / (right_x - left_x)
            return _round(config, left_y + ratio * (right_y - left_y))
    return 0.0


def _relative_scores(config, values: dict[int, float], total: int, policy: dict[str, Any]) -> dict[int, float]:
    if not values or total <= 0:
        return {}
    ranking = policy.get("ranking") if isinstance(policy.get("ranking"), dict) else policy
    reverse = str(ranking.get("order") or "desc").lower() != "asc"
    best = _num(config, ranking.get("best_score"), 100.0) or 0.0
    worst = _num(config, ranking.get("worst_score"), 0.0) or 0.0
    ordered = sorted(values.items(), key=lambda item: ((-item[1]) if reverse else item[1], item[0]))
    result: dict[int, float] = {}
    last_value: float | None = None
    last_position = 0
    for ordinal, (index, value) in enumerate(ordered, start=1):
        if last_value is None or value != last_value:
            last_value, last_position = value, ordinal
        score = best if total == 1 else best + ((last_position - 1) / (total - 1)) * (worst - best)
        result[index] = _round(config, score)
    return result


def _rank_gap(config, row: dict[str, Any], policy: dict[str, Any]) -> tuple[float, float | None, str]:
    current = _num(config, config._first(row, *(policy.get("current_rank_keys") or ["rank", "displayed_rank", "current_rank"])))
    previous = _num(config, config._first(row, *(policy.get("previous_rank_keys") or ["prev_rank", "previous_rank", "pred_rank"])))
    if current is None or previous is None or current <= 0 or previous <= 0:
        return _round(config, policy.get("missing_score", 0)), None, "missing"
    ratio = (previous - current) / previous
    rise = _num(config, policy.get("non_positive_score"), 0.0) or 0.0
    if ratio > 0:
        rise = _num(config, policy.get("default_rise_score"), 0.0) or 0.0
        for item in sorted(policy.get("rise_bands") or [], key=lambda x: -float(x.get("min", 0))):
            minimum = _num(config, item.get("min"))
            if minimum is not None and ratio >= minimum:
                rise = _num(config, item.get("score"), 0.0) or 0.0
                break
    destination = _num(config, policy.get("outside_destination_score"), 0.0) or 0.0
    for item in sorted(policy.get("destination_bands") or [], key=lambda x: float(x.get("max_rank", 10**9))):
        maximum = _num(config, item.get("max_rank"))
        if maximum is not None and current <= maximum:
            destination = _num(config, item.get("score"), 0.0) or 0.0
            break
    score = rise * (_num(config, policy.get("rise_weight"), 0.7) or 0.0)
    score += destination * (_num(config, policy.get("destination_weight"), 0.3) or 0.0)
    return _round(config, score), ratio, "ok"


def _validate_config(original, config_data: dict[str, Any]) -> list[str]:
    errors = list(original(config_data))
    criteria = config_data.get("selection_criteria")
    if not isinstance(criteria, list) or [item.get("key") for item in criteria if isinstance(item, dict)] != list(CRITERIA_KEYS):
        errors.append("selection_criteria_must_match_eight_keys")
    else:
        active = [item for item in criteria if item.get("enabled_in_final_score")]
        if [(item.get("key"), item.get("weight")) for item in active] != [("trade_value_rank", 100)]:
            errors.append("only_trade_value_rank_may_be_active")
        if any(item.get("calculation_mode") != "on_demand" for item in criteria):
            errors.append("selection_criteria_must_be_on_demand")
    policies = config_data.get("feature_policies")
    if not isinstance(policies, dict):
        errors.append("feature_policies_missing")
    else:
        for key, expected in POLICY_TYPES.items():
            policy = policies.get(key)
            if not isinstance(policy, dict) or str(policy.get("type") or "") != expected:
                errors.append(f"invalid_policy:{key}")
    contract = config_data.get("performance_contract")
    if not isinstance(contract, dict):
        errors.append("performance_contract_missing")
    else:
        for key in ("new_openapi_calls", "new_fids", "new_threads"):
            if contract.get(key) != 0:
                errors.append(f"performance_contract:{key}")
        if contract.get("browser_scoring") is not False:
            errors.append("performance_contract:browser_scoring")
    return sorted(set(errors))


def install() -> None:
    import stockboard_candidate_config as config
    import stockboard_candidate_engine as engine
    import stockboard_candidate_features as features

    cls = features.FeatureSnapshot
    if getattr(cls, "_stockboard_eight_json_policy_installed", False):
        return

    original_calculate = cls._calculate
    original_validate = config.validate_candidate_model_config

    def patched_validate(config_data: dict[str, Any]) -> list[str]:
        return _validate_config(original_validate, config_data)

    def patched_init(self, rows, feature_policies=None):
        self.rows = rows
        self.total = len(rows)
        self.feature_policies = dict(feature_policies or {})
        self.amount_ratio_values = {}
        self.trade_values = {}
        self.program_ratio_values = {}
        self.large_ratio_values = {}
        self.relative_values = {}
        self.amount_scores = {}
        self.trade_scores = {}
        self.program_scores = {}
        self.large_ratio_scores = {}
        self.relative_scores = {}
        self.board_median_rate = 0.0
        self._cache = {}
        self._json_shared_ready = False
        self._json_board_ready = False
        self.json_policy_prepare_pass_count = 0

    def prepare_shared(self):
        if self._json_shared_ready:
            return
        amount_policy = _policy(self, "amount_ratio", POLICY_TYPES["amount_ratio"])
        program_policy = _policy(self, "program_net", POLICY_TYPES["program_net"])
        large_policy = _policy(self, "large_trade", POLICY_TYPES["large_trade"])
        for index, row in enumerate(self.rows):
            trade = config._trade_value(row)
            previous = config._previous_trade_value(row)
            if trade is not None and trade > 0:
                self.trade_values[index] = trade
                if previous is not None and previous > 0:
                    self.amount_ratio_values[index] = trade / previous
            program = config._program_net(row)
            if trade is not None and trade > 0 and program is not None and program > 0:
                self.program_ratio_values[index] = program / trade
            net_key = str(large_policy.get("net_sum_key") or "large_trade_net_sum_eok")
            large_net = config._number_or_none(row.get(net_key))
            if trade is not None and trade > 0 and large_net is not None and large_net > 0:
                self.large_ratio_values[index] = large_net / trade
        self.amount_scores = _relative_scores(config, self.amount_ratio_values, self.total, amount_policy)
        self.trade_scores = _relative_scores(config, self.trade_values, self.total, amount_policy)
        self.program_scores = _relative_scores(config, self.program_ratio_values, self.total, program_policy)
        self.large_ratio_scores = _relative_scores(config, self.large_ratio_values, self.total, large_policy)
        self._json_shared_ready = True
        self.json_policy_prepare_pass_count += 1

    def prepare_board(self):
        if self._json_board_ready:
            return
        rates = [config._number_or_none(row.get("change_rate")) for row in self.rows]
        valid = sorted(value for value in rates if value is not None)
        trimmed = valid
        if len(valid) >= 20:
            cut = max(1, int(len(valid) * 0.05))
            trimmed = valid[cut:-cut] or valid
        self.board_median_rate = float(median(trimmed)) if trimmed else 0.0
        for index, rate in enumerate(rates):
            if rate is not None:
                self.relative_values[index] = rate - self.board_median_rate
        self.relative_scores = _relative_scores(config, self.relative_values, self.total, {"ranking": {"order": "desc", "best_score": 100, "worst_score": 0}})
        self._json_board_ready = True
        self.json_policy_prepare_pass_count += 1

    def strength_result(self, key: str, row: dict[str, Any]):
        policy = _policy(self, key, POLICY_TYPES[key])
        value = _num(config, config._first(row, *(policy.get("value_keys") or [])))
        if value is None or (policy.get("require_positive") and value <= 0):
            return features.FeatureResult(key, _round(config, policy.get("missing_score", 0)), value, str(policy.get("source") or POLICY), "missing")
        updated_at = config._first(row, *(policy.get("updated_at_keys") or []))
        stale_after = _num(config, policy.get("stale_after_sec"))
        age = config._age_sec(updated_at) if updated_at not in (None, "") else None
        if stale_after is not None and age is not None and age > stale_after:
            return features.FeatureResult(key, _round(config, policy.get("stale_score", 0)), value, str(policy.get("source") or POLICY), "stale")
        return features.FeatureResult(key, _piecewise(config, value, policy), value, str(policy.get("source") or POLICY), "ok")

    def large_result(self, index: int, row: dict[str, Any]):
        prepare_shared(self)
        policy = _policy(self, "large_trade", POLICY_TYPES["large_trade"])
        names = {
            "buy_count": str(policy.get("buy_count_key") or "large_trade_buy_count"),
            "sell_count": str(policy.get("sell_count_key") or "large_trade_sell_count"),
            "buy_sum": str(policy.get("buy_sum_key") or "large_trade_buy_sum_eok"),
            "sell_sum": str(policy.get("sell_sum_key") or "large_trade_sell_sum_eok"),
            "net_sum": str(policy.get("net_sum_key") or "large_trade_net_sum_eok"),
        }
        values = {name: config._number_or_none(row.get(field)) for name, field in names.items()}
        if all(value is None for value in values.values()):
            return features.FeatureResult("large_trade", _round(config, policy.get("missing_score", 0)), None, str(policy.get("source") or POLICY), "missing")
        buy_count, sell_count = values["buy_count"] or 0.0, values["sell_count"] or 0.0
        buy_sum, sell_sum = values["buy_sum"] or 0.0, values["sell_sum"] or 0.0
        net_sum = values["net_sum"] if values["net_sum"] is not None else buy_sum - sell_sum
        if policy.get("positive_net_only", True) and net_sum <= 0:
            return features.FeatureResult("large_trade", 0.0, net_sum, str(policy.get("source") or POLICY), "ok")
        amount_total, count_total = buy_sum + sell_sum, buy_count + sell_count
        amount_share = buy_sum / amount_total * 100 if amount_total > 0 else 0.0
        count_share = buy_count / count_total * 100 if count_total > 0 else 0.0
        weights = policy.get("component_weights") or {}
        score = self.large_ratio_scores.get(index, 0.0) * (_num(config, weights.get("relative_ratio"), 0.6) or 0.0)
        score += amount_share * (_num(config, weights.get("buy_amount_share"), 0.25) or 0.0)
        score += count_share * (_num(config, weights.get("buy_count_share"), 0.15) or 0.0)
        cap = 100.0
        for item in policy.get("count_caps") or []:
            maximum = _num(config, item.get("max_total_count"))
            candidate = _num(config, item.get("max_score"))
            if candidate is not None and (maximum is None or count_total <= maximum):
                cap = candidate
                break
        return features.FeatureResult("large_trade", min(_round(config, score), cap), net_sum, str(policy.get("source") or POLICY), "ok")

    def combination_result(self, index: int):
        policy = _policy(self, "combination_quality", POLICY_TYPES["combination_quality"])
        scores = {key: self.get(index, key).points for key in policy.get("dependencies") or []}
        rank_gap, amount = scores.get("rank_gap", 0), scores.get("amount_ratio", 0)
        instant, five = scores.get("instant_strength", 0), scores.get("five_min_strength", 0)
        program, large = scores.get("program_net", 0), scores.get("large_trade", 0)
        execution, flow = max(instant, five), max(program, large)
        money = 0.0
        for rule in policy.get("money_rules") or []:
            both = _num(config, rule.get("both_min"))
            either = _num(config, rule.get("either_min"))
            if both is not None and rank_gap >= both and amount >= both:
                money = _num(config, rule.get("score"), 0.0) or 0.0
                break
            if either is not None and max(rank_gap, amount) >= either:
                money = _num(config, rule.get("score"), 0.0) or 0.0
                break
        execution_flow = 0.0
        for rule in policy.get("execution_flow_rules") or []:
            emin, fmin = _num(config, rule.get("execution_min")), _num(config, rule.get("flow_min"))
            if emin is not None and execution < emin:
                continue
            if fmin is not None and flow < fmin:
                continue
            execution_flow = _num(config, rule.get("score"), 0.0) or 0.0
            break
        dual = 0.0
        for rule in policy.get("dual_flow_rules") or []:
            both, fmin = _num(config, rule.get("both_min")), _num(config, rule.get("flow_min"))
            if both is not None and program >= both and large >= both:
                dual = _num(config, rule.get("score"), 0.0) or 0.0
                break
            if fmin is not None and flow >= fmin:
                dual = _num(config, rule.get("score"), 0.0) or 0.0
                break
        strong_min = _num(config, policy.get("strong_feature_min"), 60.0) or 0.0
        strong_count = len([score for score in (rank_gap, amount, execution, program, large) if score >= strong_min])
        count_score = 0.0
        for rule in policy.get("strong_count_rules") or []:
            minimum = _num(config, rule.get("min_count"))
            if minimum is not None and strong_count >= minimum:
                count_score = _num(config, rule.get("score"), 0.0) or 0.0
                break
        weights = policy.get("component_weights") or {}
        score = money * (_num(config, weights.get("money"), 0.32) or 0.0)
        score += execution_flow * (_num(config, weights.get("execution_flow"), 0.28) or 0.0)
        score += dual * (_num(config, weights.get("dual_flow"), 0.20) or 0.0)
        score += count_score * (_num(config, weights.get("strong_count"), 0.20) or 0.0)
        return features.FeatureResult("combination_quality", _round(config, score), score, str(policy.get("source") or POLICY), "ok")

    def patched_calculate(self, index: int, key: str):
        row = self.rows[index]
        if key == "trade_value_rank":
            policy = _policy(self, key, POLICY_TYPES[key])
            rank = _num(config, row.get(str(policy.get("rank_key") or "rank")))
            if rank is None:
                rank = config._current_rank(row)
            return features.FeatureResult(key, _linear_rank(config, rank, policy), rank, str(policy.get("source") or POLICY), "missing" if rank is None else "ok")
        if key == "rank_gap":
            policy = _policy(self, key, POLICY_TYPES[key])
            points, value, status = _rank_gap(config, row, policy)
            return features.FeatureResult(key, points, value, str(policy.get("source") or POLICY), status)
        if key == "amount_ratio":
            prepare_shared(self)
            policy = _policy(self, key, POLICY_TYPES[key])
            value = self.amount_ratio_values.get(index)
            return features.FeatureResult(key, self.amount_scores.get(index, _round(config, policy.get("missing_score", 0))), value, str(policy.get("source") or POLICY), "ok" if value is not None else "missing")
        if key in {"instant_strength", "five_min_strength"}:
            return strength_result(self, key, row)
        if key == "program_net":
            prepare_shared(self)
            policy = _policy(self, key, POLICY_TYPES[key])
            value = _num(config, config._first(row, *(policy.get("value_keys") or [])))
            if value is None:
                return features.FeatureResult(key, _round(config, policy.get("missing_score", 0)), None, str(policy.get("source") or POLICY), "missing")
            updated = config._first(row, *(policy.get("updated_at_keys") or []))
            stale_after = _num(config, policy.get("stale_after_sec"))
            age = config._age_sec(updated) if updated not in (None, "") else None
            if stale_after is not None and age is not None and age > stale_after:
                return features.FeatureResult(key, _round(config, policy.get("stale_score", 0)), value, str(policy.get("source") or POLICY), "stale")
            return features.FeatureResult(key, self.program_scores.get(index, 0.0) if value > 0 else _round(config, policy.get("non_positive_score", 0)), value, str(policy.get("source") or POLICY), "ok")
        if key == "large_trade":
            return large_result(self, index, row)
        if key == "combination_quality":
            return combination_result(self, index)
        if key in {"trade_value_amount", "relative_change", "green_while_board_weak"}:
            if key == "trade_value_amount":
                prepare_shared(self)
            else:
                prepare_board(self)
        return original_calculate(self, index, key)

    config.validate_candidate_model_config = patched_validate
    engine.validate_candidate_model_config = patched_validate
    cls.__init__ = patched_init
    cls._calculate = patched_calculate
    cls._stockboard_eight_json_policy_installed = True
