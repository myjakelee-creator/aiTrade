from __future__ import annotations

import time
from typing import Any


def install(theme_module) -> None:
    """Expose coarse ThemeProjection timings without changing calculation paths.

    The instrumentation wraps only the final builder call, per-theme aggregation,
    and theme scoring. It performs no OpenAPI/TR work and adds no browser work.
    The goal is to identify whether the remaining cost is in member aggregation or
    in the surrounding flow/continuity/momentum wrappers before further changes.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_theme_timing_installed", False):
        return

    original_init = builder_class.__init__
    original_call = builder_class.__call__
    original_aggregate_theme = builder_class._aggregate_theme
    original_score_themes = builder_class._score_themes

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._theme_timing_aggregate_ms = 0.0
        self._theme_timing_aggregate_count = 0
        self._theme_timing_score_ms = 0.0

    def aggregate_theme(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_aggregate_theme(self, *args, **kwargs)
        finally:
            self._theme_timing_aggregate_ms += (
                time.perf_counter() - started
            ) * 1000.0
            self._theme_timing_aggregate_count += 1

    def score_themes(self, *args, **kwargs):
        started = time.perf_counter()
        try:
            return original_score_themes(self, *args, **kwargs)
        finally:
            self._theme_timing_score_ms += (
                time.perf_counter() - started
            ) * 1000.0

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        self._theme_timing_aggregate_ms = 0.0
        self._theme_timing_aggregate_count = 0
        self._theme_timing_score_ms = 0.0
        started = time.perf_counter()
        payload = original_call(self, feature_version, rows, meta)
        total_ms = (time.perf_counter() - started) * 1000.0

        if not isinstance(payload, dict):
            return payload

        aggregate_ms = float(self._theme_timing_aggregate_ms or 0.0)
        score_ms = float(self._theme_timing_score_ms or 0.0)
        other_ms = max(0.0, total_ms - aggregate_ms - score_ms)
        aggregate_count = int(self._theme_timing_aggregate_count or 0)

        payload["performance_breakdown"] = {
            "enabled": True,
            "total_ms": round(total_ms, 3),
            "aggregate_ms": round(aggregate_ms, 3),
            "aggregate_theme_count": aggregate_count,
            "aggregate_avg_per_theme_ms": round(
                aggregate_ms / aggregate_count if aggregate_count else 0.0,
                4,
            ),
            "score_ms": round(score_ms, 3),
            "other_ms": round(other_ms, 3),
            "timing_policy": "coarse_outer_call_aggregate_score",
        }
        payload["calculate_ms"] = round(total_ms, 3)
        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["performance_timing_enabled"] = True
            policy["performance_timing_scope"] = (
                "final_call_per_theme_aggregate_theme_score"
            )
        return payload

    builder_class.__init__ = init
    builder_class._aggregate_theme = aggregate_theme
    builder_class._score_themes = score_themes
    builder_class.__call__ = call
    builder_class._stockboard_theme_timing_installed = True
