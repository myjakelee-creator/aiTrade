from __future__ import annotations

from datetime import datetime

from realtime_v2.common import normalize_code
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "six_metric_output_guard_v1"


def install(base) -> None:
    """Remove invalid residual metric values after all restore layers have run."""

    import realtime_v2.worker_six_metric_lifecycle_patch as lifecycle

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_six_metric_output_guard_installed",
        False,
    ):
        return

    original_state_init = state_class.__init__
    original_rows = state_class.rows

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        with self.lock:
            self.status["six_metric_output_guard_installed"] = True
            self.status["six_metric_output_guard_version"] = PATCH_VERSION

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        now = datetime.now()
        session = market_session_now(now)
        expected = lifecycle._expected_date(session, now)
        removed = {group: 0 for group in lifecycle.GROUPS}
        accepted = {group: 0 for group in lifecycle.GROUPS}

        for row in result:
            if not isinstance(row, dict) or not normalize_code(row.get("stock_code")):
                continue
            for group, config in lifecycle.GROUPS.items():
                present = any(key in row for key in config["display_keys"])
                if not present:
                    continue
                source_date = (
                    lifecycle._date_digits(row.get(f"{group}_source_trading_date"))
                    or lifecycle._group_date(row, group)
                )
                valid = (
                    lifecycle._group_usable(row, group)
                    and bool(expected)
                    and source_date == expected
                )
                if valid:
                    row[f"{group}_available"] = True
                    accepted[group] += 1
                    continue
                lifecycle._clear_display(
                    row,
                    group,
                    "final_output_guard_hidden",
                )
                row[f"{group}_source_trading_date"] = source_date or None
                row[f"{group}_expected_trading_date"] = expected or None
                removed[group] += 1

        with self.lock:
            self.status["six_metric_output_guard_expected_date"] = expected
            self.status["six_metric_output_guard_phase"] = str(session.phase or "")
            for group in lifecycle.GROUPS:
                self.status[f"six_metric_output_{group}_accepted_count"] = accepted[group]
                self.status[f"six_metric_output_{group}_removed_count"] = removed[group]
        return result

    state_class.__init__ = state_init
    state_class.rows = rows
    state_class._stockboard_six_metric_output_guard_installed = True
