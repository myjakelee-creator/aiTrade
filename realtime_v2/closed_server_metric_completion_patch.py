from __future__ import annotations

"""Complete server-owned display metrics only during non-live sessions.

This patch does not touch Collector/QAx/FID, trade application, WebSocket/REST
cadence, SSE cadence, sorting, or browser calculations. It only completes
already-validated row fields after the normal State.rows() pipeline has run.
"""

from typing import Any

from realtime_v2.market_session import market_session_now

PATCH_VERSION = "closed_server_metric_completion_v2_install_chain"
CLOSED_PHASES = {"closed", "before_market", "weekend", "holiday"}


def _number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _complete_amount_ratio(row: dict[str, Any]) -> bool:
    existing = _number(row.get("amount_ratio"))
    if existing is not None and existing > 0:
        return False
    current = _number(row.get("trade_value_eok"))
    previous = _number(row.get("prev_trade_value_eok"))
    if current is None or current < 0 or previous is None or previous <= 0:
        return False
    row["amount_ratio"] = round(current / previous, 4)
    row["amount_ratio_source"] = "trade_value_current_div_previous_closed_server"
    row["amount_ratio_status"] = "closed_server_completed"
    return True


def _complete_grade_alias(row: dict[str, Any]) -> bool:
    if row.get("grade") not in (None, "", "-"):
        return False
    for key in ("candidate_grade", "candidate_grade_text"):
        value = row.get(key)
        if value not in (None, "", "-"):
            row["grade"] = value
            row["grade_display_source"] = key
            return True
    return False


def _hide_unproven_one_min_zero(row: dict[str, Any]) -> bool:
    status = str(
        row.get("one_min_status") or row.get("minute_trade_value_status") or ""
    ).strip().lower()
    proven_statuses = {
        "ok",
        "new",
        "complete",
        "completed",
        "exact",
        "exact_live",
        "same_session_last_valid",
    }
    if status in proven_statuses:
        return False
    changed = False
    for key in ("one_min_trade_value_eok", "trade_value_1m_eok"):
        value = _number(row.get(key))
        if value == 0:
            row.pop(key, None)
            changed = True
    for key in ("one_min_ratio", "trade_value_1m_ratio"):
        if _number(row.get(key)) == 0:
            row.pop(key, None)
            changed = True
    if changed:
        row["one_min_status"] = "unavailable_after_restart_without_completed_bucket"
        row["minute_trade_value_status"] = row["one_min_status"]
        row["one_min_available"] = False
    return changed


def install(base) -> None:
    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class, "_stockboard_closed_server_metric_completion_installed", False
    ):
        return

    original_rows = state_class.rows

    def rows(self, *args, **kwargs):
        result = original_rows(self, *args, **kwargs)
        session = market_session_now()
        phase = str(getattr(session, "phase", "") or "")
        if phase not in CLOSED_PHASES or not isinstance(result, list):
            return result

        amount_count = 0
        grade_count = 0
        one_min_hidden_count = 0
        for row in result:
            if not isinstance(row, dict):
                continue
            amount_count += int(_complete_amount_ratio(row))
            grade_count += int(_complete_grade_alias(row))
            one_min_hidden_count += int(_hide_unproven_one_min_zero(row))

        lock = getattr(self, "lock", None)
        status = getattr(self, "status", None)
        values = {
            "closed_server_metric_completion_installed": True,
            "closed_server_metric_completion_version": PATCH_VERSION,
            "closed_server_metric_completion_phase": phase,
            "closed_server_amount_ratio_count": amount_count,
            "closed_server_grade_alias_count": grade_count,
            "closed_server_one_min_hidden_count": one_min_hidden_count,
        }
        if lock is not None and isinstance(status, dict):
            with lock:
                status.update(values)
        elif isinstance(status, dict):
            status.update(values)
        return result

    state_class.rows = rows
    state_class._stockboard_closed_server_metric_completion_installed = True
    state_class._stockboard_closed_server_metric_completion_version = PATCH_VERSION


def install_runtime_wrapper() -> None:
    """Install on the Worker State construction chain.

    ``worker_opening_burst_cache_patch.install(base)`` is the shared runtime hook
    used by the other Worker row wrappers. Unlike the HTML global-sort installer,
    it receives the module that owns ``State``.
    """

    from realtime_v2 import worker_opening_burst_cache_patch as opening_module

    if getattr(opening_module, "_closed_server_metric_completion_install_wrapped", False):
        return
    original_install = opening_module.install

    def install_after_opening(base) -> None:
        original_install(base)
        install(base)

    opening_module.install = install_after_opening
    opening_module._closed_server_metric_completion_install_wrapped = True
