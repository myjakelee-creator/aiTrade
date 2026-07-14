from __future__ import annotations

from typing import Any


POLICY = "trade_value_rank_top100_linear_100_to_1_v1"


def trade_value_rank_points(rank: Any) -> float:
    """Return the exact requested Top100 liquidity score.

    Rank 1 receives 100 points, rank 100 receives 1 point, and ranks outside
    the current Top100 receive 0. This is independent of the internal universe
    size, so an internal universe of 170-190 symbols cannot dilute rank 100.
    """

    try:
        numeric_rank = int(float(rank))
    except (TypeError, ValueError):
        return 0.0
    if numeric_rank < 1 or numeric_rank > 100:
        return 0.0
    return float(101 - numeric_rank)


def install() -> None:
    import stockboard_candidate_config as config
    import stockboard_candidate_features as features

    snapshot_class = features.FeatureSnapshot
    if getattr(snapshot_class, "_stockboard_trade_value_rank_score_installed", False):
        return

    original_calculate = snapshot_class._calculate

    def patched_calculate(self, index: int, key: str):
        if key != "trade_value_rank":
            return original_calculate(self, index, key)

        row = self.rows[index]
        rank = config._current_rank(row)
        if rank is None or rank <= 0:
            return features.FeatureResult(
                key,
                0.0,
                None,
                POLICY,
                "missing",
            )

        points = trade_value_rank_points(rank)
        return features.FeatureResult(
            key,
            points,
            float(rank),
            POLICY,
            "ok",
        )

    snapshot_class._calculate = patched_calculate
    snapshot_class._stockboard_trade_value_rank_score_installed = True
