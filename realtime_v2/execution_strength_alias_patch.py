from __future__ import annotations

from copy import deepcopy
from typing import Any

from realtime_v2.common import now_text, to_number


def _positive(value: Any) -> float | None:
    number = to_number(value)
    if number is None or float(number) <= 0:
        return None
    return round(float(number), 4)


def install(base) -> None:
    """Expose opt10046's current strength through the standard execution key."""

    state_class = base.State
    if getattr(state_class, "_stockboard_execution_strength_alias_installed", False):
        return

    original_apply = state_class._apply_close_metrics

    def apply_close_metrics(self, event: dict[str, Any]) -> None:
        values = base.merged_event_values(event)
        instant = _positive(
            values.get("execution_strength")
            or values.get("realtime_strength_snapshot")
        )
        if instant is None:
            return original_apply(self, event)

        snapshot_at = (
            values.get("execution_strength_updated_at")
            or values.get("strength_completed_at")
            or values.get("strength_snapshot_at")
            or event.get("ts")
            or now_text()
        )
        next_event = dict(event)
        next_values = deepcopy(
            event.get("values") if isinstance(event.get("values"), dict) else {}
        )
        next_values.update(
            {
                "execution_strength": instant,
                "last_valid_execution_strength": instant,
                "execution_strength_updated_at": snapshot_at,
                "last_valid_strength_at": snapshot_at,
                "execution_strength_source": (
                    values.get("execution_strength_source")
                    or values.get("strength_source")
                    or "opt10046"
                ),
            }
        )
        next_event["values"] = next_values
        return original_apply(self, next_event)

    state_class._apply_close_metrics = apply_close_metrics
    state_class._stockboard_execution_strength_alias_installed = True
