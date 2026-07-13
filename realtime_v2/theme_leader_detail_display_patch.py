from __future__ import annotations

from typing import Any


def install(theme_module) -> None:
    """Map the selected-theme detail score cell to the server leader score.

    The original StockBoard candidate score remains available under explicit
    ``stockboard_candidate_*`` fields. This is display aliasing only; it does not
    change candidate scoring or perform browser-side calculation.
    """

    builder_class = theme_module.ThemeProjectionBuilder
    if getattr(builder_class, "_stockboard_theme_leader_detail_display_installed", False):
        return

    original_build_selected_detail = builder_class.build_selected_detail

    def build_selected_detail(
        self,
        feature_version: int,
        theme_id: str,
        rows: tuple[dict[str, Any], ...],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        payload = original_build_selected_detail(
            self, feature_version, theme_id, rows, meta
        )
        if not isinstance(payload, dict) or payload.get("status") != "READY":
            return payload
        theme = payload.get("theme")
        members = theme.get("members") if isinstance(theme, dict) else None
        if not isinstance(members, list):
            return payload

        for member in members:
            if not isinstance(member, dict):
                continue
            member["stockboard_candidate_score"] = member.get("candidate_score")
            member["stockboard_candidate_score_text"] = member.get(
                "candidate_score_text"
            )
            if member.get("leadership_score") is not None:
                member["candidate_score_text"] = member.get(
                    "leadership_score_text"
                )
            member["detail_score_label"] = "주도점수"

        policy = payload.setdefault("policy", {})
        if isinstance(policy, dict):
            policy["detail_score_display"] = "leadership_score"
            policy["stockboard_candidate_score_preserved"] = True
            policy["browser_score_calculation_allowed"] = False
        return payload

    builder_class.build_selected_detail = build_selected_detail
    builder_class._stockboard_theme_leader_detail_display_installed = True
