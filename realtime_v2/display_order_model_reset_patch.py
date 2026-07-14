from __future__ import annotations

from typing import Any

from realtime_v2.common import now_text


POLICY = "reset_lanes_on_candidate_model_change_v1"


def _model_id(rows: list[dict[str, Any]]) -> str:
    for row in rows:
        value = str(row.get("candidate_model_id") or "").strip()
        if value:
            return value
    return ""


def install() -> None:
    import stockboard_display_order as display_order

    controller_class = display_order.DisplayOrderController
    if getattr(controller_class, "_stockboard_model_reset_installed", False):
        return

    original_init = controller_class.__init__
    original_apply = controller_class.apply
    original_status = controller_class.status

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.active_candidate_model_id: str | None = None
        self.candidate_model_reset_count = 0
        self.candidate_model_last_reset_at: str | None = None

    def reset_lanes_locked(self) -> None:
        self.top_codes = []
        self.pool_codes = []
        if hasattr(self, "warm_codes"):
            self.warm_codes = []
        if hasattr(self, "cold_codes"):
            self.cold_codes = []
        self.frozen_codes = []
        self.pending_freeze = False

        for name in (
            "_challenger_since",
            "_incumbent_out_since",
            "_warm_challenger_since",
            "_warm_incumbent_out_since",
        ):
            timer_map = getattr(self, name, None)
            if isinstance(timer_map, dict):
                timer_map.clear()

        self._last_swap_monotonic = 0.0
        if hasattr(self, "_last_warm_swap_monotonic"):
            self._last_warm_swap_monotonic = 0.0
        self.last_swap = None
        if hasattr(self, "last_warm_swap"):
            self.last_warm_swap = None

    def apply(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        incoming_model_id = _model_id(rows)
        with self._lock:
            previous_model_id = getattr(self, "active_candidate_model_id", None)
            if previous_model_id is None:
                self.active_candidate_model_id = incoming_model_id or None
            elif incoming_model_id and incoming_model_id != previous_model_id:
                reset_lanes_locked(self)
                self.active_candidate_model_id = incoming_model_id
                self.candidate_model_reset_count += 1
                self.candidate_model_last_reset_at = now_text()
                self.updated_at = self.candidate_model_last_reset_at
                self.version += 1
        return original_apply(self, rows)

    def status(self) -> dict[str, Any]:
        result = dict(original_status(self))
        result.update(
            {
                "active_candidate_model_id": getattr(
                    self, "active_candidate_model_id", None
                ),
                "candidate_model_reset_count": int(
                    getattr(self, "candidate_model_reset_count", 0) or 0
                ),
                "candidate_model_last_reset_at": getattr(
                    self, "candidate_model_last_reset_at", None
                ),
                "candidate_model_reset_policy": POLICY,
            }
        )
        return result

    controller_class.__init__ = init
    controller_class.apply = apply
    controller_class.status = status
    controller_class._stockboard_model_reset_installed = True
