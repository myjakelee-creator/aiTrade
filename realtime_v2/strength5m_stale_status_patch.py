from __future__ import annotations

import time
from typing import Any


_PENDING_STATUSES = {"pending", "requested", "deferred"}


def install() -> None:
    from realtime_v2 import strength5m_scheduler as scheduler_module

    scheduler_class = scheduler_module.Strength5mScheduler
    if getattr(scheduler_class, "_stockboard_stale_status_patch_installed", False):
        return

    original_stats = scheduler_class.stats

    def preopen_candidate_due(
        self,
        item: dict[str, Any],
        *,
        final_window: bool,
        now_mono: float | None = None,
    ) -> tuple[bool, float]:
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("strength_status") or "").strip().lower()
        code = item["stock_code"]
        now_mono = time.monotonic() if now_mono is None else now_mono

        # A requested/deferred marker can survive a restart in the persisted row.
        # Only the current scheduler's own outstanding request should block a retry.
        requested_at = self.preopen_requested_at.get(code)
        if status in _PENDING_STATUSES and requested_at is not None:
            due_at = float(self.preopen_retry_at.get(code, 0.0) or 0.0)
            if now_mono < due_at:
                return False, due_at - now_mono

        if status in _PENDING_STATUSES and requested_at is None:
            released = getattr(self, "preopen_stale_status_released", None)
            if not isinstance(released, set):
                released = set()
                self.preopen_stale_status_released = released
            released.add(code)

        if final_window and code not in self.preopen_final_attempted:
            return True, float("inf")

        due_at = float(self.preopen_retry_at.get(code, 0.0) or 0.0)
        if now_mono < due_at:
            return False, due_at - now_mono

        attempt = int(self.preopen_attempts.get(code, 0) or 0)
        return True, float("inf") if attempt == 0 else float(attempt)

    def stats(self) -> dict[str, Any]:
        result = original_stats(self)
        released = getattr(self, "preopen_stale_status_released", set())
        result["stale_pending_status_release_count"] = len(released)
        result["stale_pending_status_release_sample"] = sorted(released)[:20]
        return result

    scheduler_class._preopen_candidate_due = preopen_candidate_due
    scheduler_class.stats = stats
    scheduler_class._stockboard_stale_status_patch_installed = True
