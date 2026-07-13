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


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


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


def _leadership_score(
    member: dict[str, Any],
    blocked: set[str],
) -> float:
    """Reproduce ThemeProjectionBuilder leadership scoring from one projected row.

    The previous implementation cloned the raw row and called the full member projector a
    second time whenever held metrics were blocked. During close/weekend operation most
    rows are blocked, so that doubled formatting and dict-allocation work. This function
    applies the same score formula directly to the already-projected display row.
    """

    score = 0.0
    weight = 0.0

    candidate = _number(member.get("candidate_score"))
    if candidate is not None:
        score += _clamp(candidate) * 45.0
        weight += 45.0

    change_rate = _number(member.get("change_rate"))
    if change_rate is not None:
        score += _clamp((change_rate + 5.0) * 10.0) * 20.0
        weight += 20.0

    amount_ratio = _number(member.get("amount_ratio"))
    if amount_ratio is not None:
        score += _clamp(amount_ratio * 20.0) * 15.0
        weight += 15.0

    execution = None if "execution" in blocked else _number(
        member.get("execution_strength")
    )
    if execution is not None:
        score += _clamp((execution - 70.0) / 1.3) * 10.0
        weight += 10.0

    program = None if "program" in blocked else _number(member.get("program_net"))
    large = None if "large_trade" in blocked else _number(
        member.get("large_trade_net_sum_eok")
    )
    if program is not None or large is not None:
        flow = 0.0
        divisor = 0
        if program is not None:
            flow += _clamp(50.0 + program * 2.0)
            divisor += 1
        if large is not None:
            flow += _clamp(50.0 + large * 10.0)
            divisor += 1
        score += (flow / max(1, divisor)) * 10.0
        weight += 10.0

    return round(score / weight if weight > 0 else 0.0, 2)


def install(theme_module) -> None:
    """Keep held metrics visible while excluding prior-session values from scores.

    Board metric continuity is applied to the shared FeatureSnapshot after the StockBoard
    candidate engine has completed. ThemeBoard therefore needs this explicit guard because
    it computes its own theme/leadership score. The guard never fetches data and never
    changes the display values delivered to the UI.
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

        if blocked:
            display_member["leadership_score"] = _leadership_score(
                display_member,
                blocked,
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

        program_total = 0.0
        large_total = 0.0
        program_found = False
        large_found = False
        held_count = 0
        for member in members:
            if not isinstance(member, dict):
                continue
            weight = _number(member.get("master_weight")) or 1.0
            if weight <= 0:
                continue

            program = _number(member.get("_theme_score_program_net"))
            if program is not None:
                program_total += program * weight
                program_found = True

            large = _number(member.get("_theme_score_large_trade_net"))
            if large is not None:
                large_total += large * weight
                large_found = True

            if member.get("metric_scoring_blocked_groups"):
                held_count += 1

        result["_theme_score_program_net_eok"] = (
            round(program_total, 4) if program_found else None
        )
        result["_theme_score_large_trade_net_eok"] = (
            round(large_total, 4) if large_found else None
        )
        result["held_member_count"] = held_count
        result["data_basis_text"] = (
            "전일 보존값 포함·점수 제외" if held_count else "현재/마감 보존값"
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
            policy["continuity_member_reprojection_allowed"] = False

        details = payload.get("details")
        if isinstance(details, dict):
            for theme in details.values():
                if not isinstance(theme, dict):
                    continue
                theme.pop("_theme_score_program_net_eok", None)
                theme.pop("_theme_score_large_trade_net_eok", None)
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

        payload["continuity_guard_status"] = {
            "enabled": True,
            "member_reprojection_count": 0,
            "aggregate_member_passes": 1,
            "mode": "single_projection_direct_leadership_rescore",
        }
        return payload

    builder_class._member_row = member_row
    builder_class._aggregate_theme = aggregate_theme
    builder_class._score_themes = score_themes
    builder_class.__call__ = call
    builder_class._stockboard_continuity_guard_installed = True
