from __future__ import annotations

from typing import Any


_ACCOUNTED_KEYS = (
    "aggregate_ms",
    "score_sort_ms",
    "momentum_ms",
    "dual_rank_ms",
    "leader_rank_ms",
)


def install(theme_module) -> None:
    """Correct Theme projection phase accounting without adding calculation work.

    Multiple wrappers add their own phase timings. The outer dual-rank wrapper used to
    classify the already-measured leader phase as ``other_ms``. This patch only recomputes
    the final accounting from the existing completed payload.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_theme_performance_accounting_installed", False):
        return

    original_call = builder_class.__call__

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        payload = original_call(self, feature_version, rows, meta)
        if not isinstance(payload, dict):
            return payload
        performance = payload.get("performance_breakdown")
        if not isinstance(performance, dict):
            return payload
        try:
            total_ms = float(payload.get("calculate_ms") or performance.get("total_ms") or 0.0)
        except (TypeError, ValueError):
            return payload
        accounted_ms = 0.0
        for key in _ACCOUNTED_KEYS:
            try:
                accounted_ms += float(performance.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
        performance["accounted_ms"] = round(accounted_ms, 3)
        performance["other_ms"] = round(max(0.0, total_ms - accounted_ms), 3)
        performance["accounting_policy"] = "final_total_minus_named_nonoverlapping_phases"
        return payload

    builder_class.__call__ = call
    builder_class._stockboard_theme_performance_accounting_installed = True
