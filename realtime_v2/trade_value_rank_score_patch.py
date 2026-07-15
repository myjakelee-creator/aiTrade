"""Compatibility helpers for the JSON-driven trade-value rank policy.

The production engine passes ``feature_policies`` from the final model JSON
straight into ``FeatureSnapshot``. ``install`` remains a no-op so older runtime
import order stays harmless while no Python constant overrides the JSON policy.
"""
from __future__ import annotations

from typing import Any

from stockboard_candidate_config import load_candidate_model_config
from stockboard_candidate_features import linear_rank_points

POLICY = "candidate_model_json"


def trade_value_rank_points(
    rank: Any,
    policy: dict[str, Any] | None = None,
) -> float:
    selected_policy = policy
    if selected_policy is None:
        config = load_candidate_model_config()
        selected_policy = dict(config.get("feature_policies", {}).get("trade_value_rank") or {})
    return linear_rank_points(rank, selected_policy)


def install() -> None:
    """Retained for import compatibility; no runtime monkey patch is installed."""

    return None
