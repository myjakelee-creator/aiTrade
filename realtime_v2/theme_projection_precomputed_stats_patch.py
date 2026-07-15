from __future__ import annotations

from statistics import median
from typing import Any


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def install(theme_module) -> None:
    """Precompute median change/amount-ratio without rescanning theme details.

    The existing aggregation already visits each active member once. This patch records
    only the two scalar values needed by the approved trend features during that pass,
    then writes the medians onto the completed theme row. It adds no OpenAPI/TR work,
    no browser calculation, and no additional pass over all detail members.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_precomputed_stats_installed", False):
        return

    original_member_row = getattr(builder_class, "_member_row", None)
    original_aggregate_theme = getattr(builder_class, "_aggregate_theme", None)
    if not callable(original_member_row) or not callable(original_aggregate_theme):
        return

    sentinel = object()

    def member_row(self, row: dict[str, Any], member: dict[str, Any]) -> dict[str, Any]:
        projected = original_member_row(self, row, member)
        accumulator = getattr(self, "_theme_precomputed_stats_accumulator", None)
        if isinstance(accumulator, list):
            accumulator.append(
                (
                    _number(projected.get("change_rate")),
                    _number(projected.get("amount_ratio")),
                )
            )
        return projected

    def aggregate_theme(
        self,
        theme: dict[str, Any],
        by_code: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        previous = getattr(self, "_theme_precomputed_stats_accumulator", sentinel)
        accumulator: list[tuple[float | None, float | None]] = []
        self._theme_precomputed_stats_accumulator = accumulator
        try:
            result = original_aggregate_theme(self, theme, by_code)
        finally:
            if previous is sentinel:
                try:
                    delattr(self, "_theme_precomputed_stats_accumulator")
                except AttributeError:
                    pass
            else:
                self._theme_precomputed_stats_accumulator = previous

        if not isinstance(result, dict):
            return result

        rates = [value for value, _ratio in accumulator if value is not None]
        ratios = [
            value
            for _rate, value in accumulator
            if value is not None and value > 0
        ]
        active_count = int(result.get("active_member_count") or len(accumulator) or 0)
        median_rate = float(median(rates)) if rates else None
        amount_ratio = float(median(ratios)) if ratios else None
        coverage = round(len(ratios) / active_count * 100.0, 2) if active_count else 0.0

        result.update(
            {
                "median_change_rate": (
                    round(median_rate, 4) if median_rate is not None else None
                ),
                "median_change_rate_text": (
                    "-" if median_rate is None else f"{median_rate:+.2f}%"
                ),
                "theme_amount_ratio": (
                    round(amount_ratio, 4) if amount_ratio is not None else None
                ),
                "theme_amount_ratio_text": (
                    "-" if amount_ratio is None else f"{amount_ratio:.2f}x"
                ),
                "amount_ratio_member_count": len(ratios),
                "amount_ratio_coverage_pct": coverage,
                "amount_ratio_coverage_text": f"{coverage:.0f}%",
                "theme_member_stats_basis": "single_aggregation_pass",
            }
        )
        return result

    builder_class._member_row = member_row
    builder_class._aggregate_theme = aggregate_theme
    builder_class._stockboard_precomputed_stats_installed = True
