from __future__ import annotations

import time

from realtime_v2 import after_close_recovery as recovery

POLICY_GUARD_VERSION = "after_close_recovery_policy_guard_v1"


def install() -> None:
    if getattr(recovery, "_after_close_recovery_policy_guard_installed", False):
        return

    sampler = recovery.CloseWindowSamplerService

    @staticmethod
    def nearest_before(samples, target):
        before = [sample for sample in samples if sample[0] <= target]
        return max(before, key=lambda item: item[0]) if before else None

    sampler._nearest = nearest_before

    coordinator = recovery.AfterCloseRecoveryCoordinator
    original_refresh = coordinator._refresh
    original_stats = coordinator.stats

    def refresh(self, now_mono):
        original_refresh(self, now_mono)
        if not getattr(self, "plan", None):
            return

        existing = set(getattr(self, "queue", ()))
        if getattr(self, "current", None):
            existing.add(self.current)

        target_date = self.target_date()
        added = 0
        missing_total = 0
        for item in self.plan:
            code = item["stock_code"]
            lane = item["lane"]
            row = item["row"]
            try:
                need_order = bool(self._needs(row)[2])
            except Exception:
                need_order = False
            if not need_order:
                continue
            missing_total += 1
            task = ("orderbook", code)
            if task in existing:
                continue
            if ("orderbook", code, target_date) in self.completed:
                continue
            if now_mono < self.retry_at.get(task, 0):
                continue
            if now_mono < self.settle_until.get(task, 0):
                continue
            self.queue.append(task)
            existing.add(task)
            added += 1

        self.missing_orderbook_count = missing_total
        self.orderbook_all_lane_enqueue_count = int(
            getattr(self, "orderbook_all_lane_enqueue_count", 0)
        ) + added
        if added and self.mode == "complete":
            self.mode = "queued_orderbook_all_lanes"

    def stats(self):
        result = original_stats(self)
        result.update(
            {
                "policy_guard": POLICY_GUARD_VERSION,
                "sampler_nearest_policy": "target_time_or_earlier_only",
                "orderbook_recovery_lanes": "P0-P6",
                "orderbook_all_lane_enqueue_count": int(
                    getattr(self, "orderbook_all_lane_enqueue_count", 0)
                ),
            }
        )
        return result

    coordinator._refresh = refresh
    coordinator.stats = stats
    recovery._after_close_recovery_policy_guard_installed = True
