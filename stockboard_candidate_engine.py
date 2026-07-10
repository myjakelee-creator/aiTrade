"""Config-driven candidate scoring, funnel execution, and public compatibility API."""
from __future__ import annotations
from typing import Any, Iterable

from stockboard_candidate_config import (
    FEATURE_LABELS, FIVE_FACTOR_FLOW_V01, NET_BUY_STRENGTH_TOTAL_POINTS,
    NET_BUY_STRENGTH_V02, _current_rank, _number_or_none, _now_text,
    _round_score, _score_text, grade_for_percent, grade_text_for_percent,
    load_candidate_model_config, validate_candidate_model_config,
)
from stockboard_candidate_features import FeatureSnapshot


class ConfigDrivenCandidateRankingEngine:
    def __init__(self, config: dict[str, Any]):
        self.config = dict(config or {})
        errors = validate_candidate_model_config(self.config)
        if errors:
            raise ValueError(f"invalid candidate model config {self.config.get('id')}: {errors}")
        self.model_id = str(self.config.get("id"))
        self.model_name = str(self.config.get("label") or self.config.get("name") or self.model_id)

    def enrich(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched = [dict(row) for row in rows]
        for index, row in enumerate(enriched):
            row["_source_rank"] = _current_rank(row) or index + 1
            row.setdefault("trade_value_rank", row["_source_rank"])
        snapshot = FeatureSnapshot(enriched)
        structure = self.config["score_structure"]
        required = [str(key) for key in self.config.get("required_features") or []]
        for index, row in enumerate(enriched):
            stage_results: dict[str, tuple[float, list[dict[str, Any]], float]] = {}
            for group in ("final_score", "entry_score", "confirmation_score", "focus_score"):
                stage_results[group] = self._group_score(snapshot, index, structure[group])
            raw_score, final_items, coverage = stage_results["final_score"]
            required_missing = [key for key in required if snapshot.get(index, key).status in {"missing", "stale"}]
            score = raw_score
            if coverage < .60 or required_missing:
                score = min(score, 59.0)
            elif coverage < .75:
                score = min(score, 69.0)
            elif coverage < .90:
                score = min(score, 79.0)
            guard_failures: list[str] = []
            for guard in self.config.get("grade_guards") or []:
                if not self._guard_passes(snapshot, index, guard):
                    score = min(score, float(guard.get("max_score", 89)))
                    guard_failures.append(str(guard.get("name") or guard.get("type")))
            score = _round_score(score)
            grade, grade_class = grade_for_percent(score)
            status = "WAIT_DATA" if coverage < .60 or required_missing else "READY" if score >= 80 else "WATCH" if score >= 70 else "EARLY" if score >= 60 else "WEAK"
            row.update({
                "candidate_model_id": self.model_id,
                "candidate_model_name": self.model_name,
                "candidate_score_version": self.model_id,
                "entry_score": stage_results["entry_score"][0],
                "confirmation_score": stage_results["confirmation_score"][0],
                "focus_score": stage_results["focus_score"][0],
                "score_top50": stage_results["entry_score"][0],
                "score_top20": stage_results["confirmation_score"][0],
                "score_top5": stage_results["focus_score"][0],
                "candidate_score_raw": raw_score,
                "candidate_score": score,
                "score_percent": score,
                "grade_score": score,
                "candidate_grade": grade,
                "candidate_grade_text": grade_text_for_percent(score),
                "candidate_grade_class": grade_class,
                "display_grade_source": "candidate_model_config_v3",
                "score_total": score,
                "score_total_points": score,
                "score_possible_points": 100,
                "score_status": "stale" if any(item.get("status") == "stale" for item in final_items) else "partial" if coverage < 1.0 else "ok",
                "score_updated_at": _now_text(),
                "candidate_score_coverage": round(coverage, 2),
                "candidate_status": status,
                "required_feature_missing": required_missing,
                "grade_guard_failures": guard_failures,
                "score_breakdown": {
                    "candidate_model": {
                        "id": self.model_id, "name": self.model_name,
                        "entry_score": stage_results["entry_score"][0],
                        "confirmation_score": stage_results["confirmation_score"][0],
                        "focus_score": stage_results["focus_score"][0],
                        "items": final_items,
                    },
                    "total": {"score": score, "raw_score": raw_score, "possible_points": 100, "percent": score, "grade": grade_text_for_percent(score)},
                },
                "candidate_score_items": {
                    "final_score": {item["key"]: item["points"] for item in final_items},
                    "entry_score": {item["key"]: item["points"] for item in stage_results["entry_score"][1]},
                    "confirmation_score": {item["key"]: item["points"] for item in stage_results["confirmation_score"][1]},
                    "focus_score": {item["key"]: item["points"] for item in stage_results["focus_score"][1]},
                },
                "momentum": self._momentum(final_items),
                "candidate_reason": self._reason(final_items),
                "model_validation_status": "READY",
            })
        return self._apply_funnel(enriched)

    def _group_score(self, snapshot: FeatureSnapshot, index: int, items: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]], float]:
        total = 0.0
        covered_weight = 0.0
        positive_weight = 0.0
        results: list[dict[str, Any]] = []
        for item in items:
            key = str(item.get("key"))
            weight = float(item.get("weight"))
            feature = snapshot.get(index, key)
            label = str(item.get("label") or FEATURE_LABELS.get(key) or key)
            result = feature.item(label=label, weight=weight)
            results.append(result)
            total += feature.points * weight / 100.0
            if weight > 0:
                positive_weight += weight
                if feature.status not in {"missing", "stale"}:
                    covered_weight += weight
        coverage = covered_weight / positive_weight if positive_weight else 0.0
        return _round_score(total), results, coverage

    def _guard_passes(self, snapshot: FeatureSnapshot, index: int, guard: dict[str, Any]) -> bool:
        kind = str(guard.get("type"))
        keys = [str(key) for key in (guard.get("keys") or [])]
        key = str(guard.get("key") or "")
        threshold = float(guard.get("value", 0))
        if kind == "feature_value_min":
            value = snapshot.get(index, key).value
            return value is not None and value >= threshold
        if kind == "feature_value_max":
            value = snapshot.get(index, key).value
            return value is not None and value <= threshold
        if kind == "feature_score_min":
            return snapshot.get(index, key).points >= threshold
        if kind == "any_value_min":
            return any((snapshot.get(index, item).value is not None and snapshot.get(index, item).value >= threshold) for item in keys)
        if kind == "any_positive":
            return any((snapshot.get(index, item).value or 0) > 0 for item in keys)
        if kind == "all_nonnegative":
            values = [snapshot.get(index, item).value for item in keys]
            return all(value is not None and value >= 0 for value in values)
        return False

    def _apply_funnel(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        funnel = self.config.get("funnel") if isinstance(self.config.get("funnel"), dict) else {}
        top50_take = int((funnel.get("top50") or {}).get("take") or 50)
        top20_take = int((funnel.get("top20") or {}).get("take") or 20)
        top5_take = int((funnel.get("top5") or {}).get("take") or 5)
        source = sorted(rows, key=lambda row: (row.get("candidate_status") == "WAIT_DATA", -(row.get("entry_score") or 0), -(row.get("candidate_score") or 0), row.get("_source_rank") or 999999))
        top50 = source[:top50_take]
        rest = source[top50_take:]
        top50_sorted = sorted(top50, key=lambda row: (row.get("candidate_status") == "WAIT_DATA", -(row.get("confirmation_score") or 0), -(row.get("entry_score") or 0), row.get("_source_rank") or 999999))
        top20 = top50_sorted[:top20_take]
        remaining50 = top50_sorted[top20_take:]
        top20_sorted = sorted(top20, key=lambda row: (row.get("candidate_status") == "WAIT_DATA", -(row.get("focus_score") or 0), -(row.get("candidate_score") or 0), row.get("_source_rank") or 99999))
        top5 = top20_sorted[:top5_take]
        remaining20 = top20_sorted[top5_take:]
        ordered = [*top5, *remaining20, *remaining50, *rest]
        entry_rank = {id(row): rank for rank, row in enumerate(source, 1)}
        confirmation_rank = {id(row): rank for rank, row in enumerate(top50_sorted, 1)}
        focus_rank = {id(row): rank for rank, row in enumerate(top20_sorted, 1)}
        for rank, row in enumerate(ordered, 1):
            row["entry_rank"] = entry_rank.get(id(row))
            row["confirmation_rank"] = confirmation_rank.get(id(row))
            row["focus_rank"] = focus_rank.get(id(row))
            row["model_rank"] = rank
            row["pool_rank"] = rank
            row["funnel_rank"] = rank
            row["pool_stage"] = "top20" if rank <= top20_take else "top50" if rank <= top50_take else "top300"
            row["is_candidate"] = rank <= top5_take and row.get("candidate_status") != "WAIT_DATA"
            row["candidate_rank"] = rank if row["is_candidate"] else None
            row["desired_top20"] = rank <= top20_take and row.get("candidate_status") != "WAIT_DATA"
        return ordered

    def _momentum(self, items: list[dict[str, Any]]) -> str:
        labels = [str(item.get("label")) for item in items if (_number_or_none(item.get("points")) or 0) >= 70 and (_number_or_none(item.get("weight")) or 0) > 0]
        return " + ".join(labels[:4]) if labels else "선발신호 약"

    def _reason(self, items: list[dict[str, Any]]) -> str:
        return " + ".join(f"{item.get('label')} {_score_text(item.get('points'))}점 " for item in items)


class FiveFactorFlowV01RankingEngine(ConfigDrivenCandidateRankingEngine):
    def enrich(self, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result = super().enrich(rows)
        for row in result:
            items = row.get("score_breakdown", {}).get("candidate_model", {}).get("items", [])
            by_key = {item.get("key"): item for item in items}
            row["rank_rise_score"] = (by_key.get("rank_gap") or {}).get("points", 0)
            row["amount_ratio_score"] = (by_key.get("amount_ratio") or {}).get("points", 0)
            row["instant_strength_score"] = (by_key.get("instant_strength") or {}).get("points", 0)
            row["program_score"] = (by_key.get("program_net") or {}).get("points", 0)
            row["large_trade_score"] = (by_key.get("large_trade") or {}).get("points", 0)
            combo = by_key.get("combination_quality") or {}
            row["combination_score"] = round((combo.get("points") or 0) * (combo.get("weight") or 0) / 100.0, 2)
        return result


class NetBuyStrengthV02RankingEngine(ConfigDrivenCandidateRankingEngine):
    pass


def enrich_net_buy_strength_v02_fields(rows: Iterable[dict[str, Any]], model: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    config = model or load_candidate_model_config(NET_BUY_STRENGTH_V02)
    result = NetBuyStrengthV02RankingEngine(config).enrich(rows)
    for row in result:
        items = list(row.get("score_breakdown", {}).get("candidate_model", {}).get("items", []))
        total_points = round((row.get("candidate_score") or 0) * 7, 2)
        row["score_total_points"] = total_points
        row["score_possible_points"] = NET_BUY_STRENGTH_TOTAL_POINTS
        row["score_breakdown"]["net_buy_strength"] = {
            "score": total_points,
            "possible_points": NET_BUY_STRENGTH_TOTAL_POINTS,
            "percent": row.get("candidate_score"),
            "items": items,
        }
    return result


def enrich_candidate_model_fields(rows: Iterable[dict[str, Any]], model_id: str | None = None) -> list[dict[str, Any]]:
    config = load_candidate_model_config(model_id)
    selected_id = str(config.get("id"))
    if selected_id == FIVE_FACTOR_FLOW_V01:
        return FiveFactorFlowV01RankingEngine(config).enrich(rows)
    if selected_id == NET_BUY_STRENGTH_V02:
        return enrich_net_buy_strength_v02_fields(rows, config)
    return ConfigDrivenCandidateRankingEngine(config).enrich(rows)
