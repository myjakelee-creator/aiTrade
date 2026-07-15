from __future__ import annotations

import time
from typing import Any


def install(theme_module) -> None:
    """Preserve the inner summary-core timing before outer wrappers overwrite totals."""

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_theme_core_timing_installed", False):
        return

    original_call = builder_class.__call__

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        started = time.perf_counter()
        payload = original_call(self, feature_version, rows, meta)
        core_ms = (time.perf_counter() - started) * 1000.0
        if not isinstance(payload, dict):
            return payload
        performance = payload.setdefault("performance_breakdown", {})
        if not isinstance(performance, dict):
            return payload
        aggregate_ms = float(performance.get("aggregate_ms") or 0.0)
        score_ms = float(performance.get("score_sort_ms") or 0.0)
        performance["summary_core_ms"] = round(core_ms, 3)
        performance["summary_core_other_ms"] = round(
            max(0.0, core_ms - aggregate_ms - score_ms),
            3,
        )
        performance["summary_core_timing_policy"] = (
            "inner_summary_before_flow_momentum_rank_wrappers"
        )
        return payload

    builder_class.__call__ = call
    builder_class._stockboard_theme_core_timing_installed = True
