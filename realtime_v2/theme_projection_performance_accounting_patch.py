from __future__ import annotations

from typing import Any

from realtime_v2 import theme_projection_flow_history_patch as theme_flow_module
from realtime_v2.theme_projection_fast_primitives_patch import (
    install as install_theme_fast_primitives,
)


_ACCOUNTED_KEYS = (
    "aggregate_ms",
    "score_sort_ms",
    "summary_core_other_ms",
    "momentum_ms",
    "dual_rank_ms",
    "leader_rank_ms",
)


def _float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def install(theme_module) -> None:
    """Correct final Theme timing and expose the remaining wrapper residual.

    ``summary_core_ms`` is captured before flow-history, continuity, momentum and rank
    wrappers run. Its aggregate, score and residual pieces are non-overlapping. The
    final ``other_ms`` therefore represents only work that still lacks a named phase.
    """

    # Install after all wrappers are defined but before State creates the runtime
    # builder. Summary closures resolve theme_module._code dynamically, and the flow
    # wrapper resolves its module-level _code dynamically, so both hot paths benefit.
    install_theme_fast_primitives(theme_module, theme_flow_module)

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

        total_ms = _float(payload.get("calculate_ms") or performance.get("total_ms"))
        accounted_ms = sum(_float(performance.get(key)) for key in _ACCOUNTED_KEYS)
        residual_ms = max(0.0, total_ms - accounted_ms)

        summary_core_ms = _float(performance.get("summary_core_ms"))
        post_core_wrapper_ms = max(
            0.0,
            total_ms
            - summary_core_ms
            - _float(performance.get("momentum_ms"))
            - _float(performance.get("dual_rank_ms"))
            - _float(performance.get("leader_rank_ms")),
        )

        performance["accounted_ms"] = round(accounted_ms, 3)
        performance["other_ms"] = round(residual_ms, 3)
        performance["wrapper_residual_ms"] = round(residual_ms, 3)
        performance["post_core_wrapper_ms"] = round(post_core_wrapper_ms, 3)
        performance["accounting_policy"] = (
            "final_total_minus_named_nonoverlapping_core_and_rank_phases"
        )
        return payload

    builder_class.__call__ = call
    builder_class._stockboard_theme_performance_accounting_installed = True
