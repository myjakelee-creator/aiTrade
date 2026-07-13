from __future__ import annotations

from typing import Any


_SCORE_ONLY_KEYS = (
    "_theme_score_program_net",
    "_theme_score_large_trade_net",
    "_theme_score_execution_strength",
)


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _blocked_groups(row: dict[str, Any]) -> set[str]:
    raw = row.get("metric_scoring_blocked_groups")
    if isinstance(raw, (list, tuple, set)):
        return {str(value or "").strip().lower() for value in raw if value}
    return set()


def _basis_text(row: dict[str, Any]) -> str:
    basis = str(row.get("metric_continuity_basis") or "").strip()
    if basis == "current_session":
        return "현재 세션"
    if basis == "last_session_hold":
        return "직전 세션 유지"
    if basis == "previous_session_hold":
        return "전일값 유지·점수 제외"
    return basis or "-"


def _weighted_total(
    members: list[dict[str, Any]],
    key: str,
) -> float | None:
    values: list[tuple[float, float]] = []
    for member in members:
        value = _number(member.get(key))
        weight = _number(member.get("master_weight")) or 1.0
        if value is None or weight <= 0:
            continue
        values.append((value, weight))
    if not values:
        return None
    return round(sum(value * weight for value, weight in values), 4)


def install(theme_module) -> None:
    """Keep held metrics visible while excluding prior-session values from scores.

    Board metric continuity is applied to the shared FeatureSnapshot after the
    StockBoard candidate engine has completed. ThemeBoard therefore needs this
    explicit guard because it computes its own theme/leadership score. The guard
    never fetches data and never changes the display values delivered to the UI.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_continuity_guard_installed", False):
        return

    original_member_row = builder_class._member_row
    original_aggregate_theme = builder_class._aggregate_theme
    original_score_themes = builder_class._score_themes
    original_call = builder_class.__call__

    def member_row(
        self,
        row: dict[str, Any],
        member: dict[str, Any],
    ) -> dict[str, Any]:
        display_member = original_member_row(self, row, member)
        blocked = _blocked_groups(row)

        score_row = dict(row)
        if "execution" in blocked:
            score_row["execution_strength"] = None
        if "program" in blocked:
            score_row["program_net"] = None
        if "large_trade" in blocked:
            score_row["large_trade_net_sum_eok"] = None
            score_row["large_trade_net_count"] = None

        if blocked:
            score_member = original_member_row(self, score_row, member)
            display_member["leadership_score"] = score_member.get(
                "leadership_score"
            )

        display_member["_theme_score_program_net"] = (
            None if "program" in blocked else display_member.get("program_net")
        )
        display_member["_theme_score_large_trade_net"] = (
            None
            if "large_trade" in blocked
            else display_member.get("large_trade_net_sum_eok")
        )
        display_member["_theme_score_execution_strength"] = (
            None
            if "execution" in blocked
            else display_member.get("execution_strength")
        )
        display_member["metric_scoring_blocked_groups"] = sorted(blocked)
        display_member["metric_basis_text"] = _basis_text(row)
        display_member["metric_continuity_basis"] = row.get(
            "metric_continuity_basis"
        )
        display_member["metric_continuity_reference_date"] = row.get(
            "metric_continuity_reference_date"
        )
        display_member["metric_continuity_valid_until"] = row.get(
            "metric_continuity_valid_until"
        )
        return display_member

    def aggregate_theme(
        self,
        theme: dict[str, Any],
        by_code: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        result = original_aggregate_theme(self, theme, by_code)
        members = result.get("members") if isinstance(result, dict) else None
        members = members if isinstance(members, list) else []
        result["_theme_score_program_net_eok"] = _weighted_total(
            members, "_theme_score_program_net"
        )
        result["_theme_score_large_trade_net_eok"] = _weighted_total(
            members, "_theme_score_large_trade_net"
        )
        held_members = [
            member
            for member in members
            if member.get("metric_scoring_blocked_groups")
        ]
        result["held_member_count"] = len(held_members)
        result["data_basis_text"] = (
            "전일 보존값 포함·점수 제외"
            if held_members
            else "현재/마감 보존값"
        )
        return result

    def score_themes(self, themes: list[dict[str, Any]]) -> None:
        saved: list[tuple[dict[str, Any], Any, Any]] = []
        for theme in themes:
            saved.append(
                (
                    theme,
                    theme.get("program_net_eok"),
                    theme.get("large_trade_net_eok"),
                )
            )
            theme["program_net_eok"] = theme.get(
                "_theme_score_program_net_eok"
            )
            theme["large_trade_net_eok"] = theme.get(
                "_theme_score_large_trade_net_eok"
            )
        try:
            original_score_themes(self, themes)
        finally:
            for theme, display_program, display_large in saved:
                theme["program_net_eok"] = display_program
                theme["large_trade_net_eok"] = display_large

    def call(
        self,
        feature_version: int,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        payload = original_call(self, feature_version, rows, meta)
        if not isinstance(payload, dict):
            return payload

        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["held_metric_display_allowed"] = True
            policy["previous_session_metric_scoring_allowed"] = False
            policy["continuity_input"] = "shared_feature_snapshot_only"

        details = payload.get("details")
        if isinstance(details, dict):
            for theme in details.values():
                if not isinstance(theme, dict):
                    continue
                for key in (
                    "_theme_score_program_net_eok",
                    "_theme_score_large_trade_net_eok",
                ):
                    theme.pop(key, None)
                members = theme.get("members")
                if not isinstance(members, list):
                    continue
                for member in members:
                    if not isinstance(member, dict):
                        continue
                    for key in _SCORE_ONLY_KEYS:
                        member.pop(key, None)

        summaries = payload.get("rows")
        if isinstance(summaries, list):
            for theme in summaries:
                if not isinstance(theme, dict):
                    continue
                theme.pop("_theme_score_program_net_eok", None)
                theme.pop("_theme_score_large_trade_net_eok", None)
        return payload

    builder_class._member_row = member_row
    builder_class._aggregate_theme = aggregate_theme
    builder_class._score_themes = score_themes
    builder_class.__call__ = call
    builder_class._stockboard_continuity_guard_installed = True
