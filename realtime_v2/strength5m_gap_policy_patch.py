from __future__ import annotations

import time
from typing import Any


def install() -> None:
    """Prevent unrelated TR timestamps from starving premarket strength backfill."""

    from realtime_v2 import strength5m_scheduler as scheduler_module

    scheduler_class = scheduler_module.Strength5mScheduler
    if getattr(scheduler_class, "_stockboard_gap_policy_patch_installed", False):
        return

    original_idle = scheduler_class._idle
    original_stats = scheduler_class.stats

    def ensure_fields(self) -> None:
        defaults = {
            "preopen_strength_gap_remaining_sec": None,
            "preopen_cross_tr_gap_remaining_sec": None,
            "preopen_strength_last_age_sec": None,
            "preopen_orderbook_last_age_sec": None,
            "preopen_opt10055_last_age_sec": None,
            "preopen_close_metrics_last_age_sec": None,
            "preopen_gap_policy": "strength_10s_cross_tr_normal_gap",
        }
        for name, value in defaults.items():
            if not hasattr(self, name):
                setattr(self, name, value)

    def age_sec(now_mono: float, value: Any) -> float | None:
        try:
            timestamp = float(value or 0.0)
        except (TypeError, ValueError):
            return None
        if timestamp <= 0:
            return None
        return round(max(0.0, now_mono - timestamp), 3)

    def idle(self, session=None, gap_override: float | None = None) -> bool:
        ensure_fields(self)
        result = original_idle(self, session, gap_override=gap_override)
        if result:
            self.preopen_strength_gap_remaining_sec = 0.0
            self.preopen_cross_tr_gap_remaining_sec = 0.0
            return True

        if not self._preopen_window(session):
            return False
        if getattr(self, "preopen_block_reason", None) != "global_request_gap":
            return False

        provider = self.provider
        lock = getattr(provider, "_lock", None)
        if lock is None:
            self.preopen_block_reason = "provider_lock_missing"
            return False

        now_mono = time.monotonic()
        with lock:
            # Re-check actual work before relaxing only the timestamp policy.
            inflight_checks = (
                ("strength_inflight", getattr(provider, "_strength_probe_inflight", None)),
                ("orderbook_inflight", getattr(provider, "_orderbook_probe_inflight", None)),
                ("opt10055_inflight", getattr(provider, "_opt10055_probe_inflight", None)),
            )
            for reason, value in inflight_checks:
                if value:
                    self.preopen_block_reason = reason
                    return False

            queue_checks = (
                ("strength_pending", getattr(provider, "_strength_probe_pending", ())),
                ("orderbook_pending", getattr(provider, "_orderbook_probe_pending", ())),
                ("opt10055_pending", getattr(provider, "_opt10055_probe_pending", ())),
            )
            for reason, value in queue_checks:
                if len(value):
                    self.preopen_block_reason = reason
                    return False

            strength_last = float(
                getattr(provider, "_strength_probe_last_request_at", 0.0) or 0.0
            )
            orderbook_last = float(
                getattr(provider, "_orderbook_probe_last_request_at", 0.0) or 0.0
            )
            opt10055_last = float(
                getattr(provider, "_opt10055_probe_last_request_at", 0.0) or 0.0
            )
            close_metrics_last = float(
                getattr(provider, "_close_metrics_last_request_at", 0.0) or 0.0
            )

        strength_gap = (
            self.gap(session)
            if gap_override is None
            else max(self.normal_gap, float(gap_override))
        )
        # Other real TRs only need the normal global safety gap. The legacy
        # close-metrics timestamp is excluded because that queue is not a COM TR.
        cross_tr_gap = max(1.05, float(self.normal_gap))
        cross_tr_last = max(orderbook_last, opt10055_last)

        strength_age = age_sec(now_mono, strength_last)
        cross_tr_age = age_sec(now_mono, cross_tr_last)
        strength_remaining = (
            0.0
            if strength_age is None
            else round(max(0.0, strength_gap - strength_age), 3)
        )
        cross_tr_remaining = (
            0.0
            if cross_tr_age is None
            else round(max(0.0, cross_tr_gap - cross_tr_age), 3)
        )

        self.preopen_strength_last_age_sec = strength_age
        self.preopen_orderbook_last_age_sec = age_sec(now_mono, orderbook_last)
        self.preopen_opt10055_last_age_sec = age_sec(now_mono, opt10055_last)
        self.preopen_close_metrics_last_age_sec = age_sec(now_mono, close_metrics_last)
        self.preopen_strength_gap_remaining_sec = strength_remaining
        self.preopen_cross_tr_gap_remaining_sec = cross_tr_remaining

        if strength_remaining > 0:
            self.preopen_block_reason = "strength_request_gap"
            return False
        if cross_tr_remaining > 0:
            self.preopen_block_reason = "cross_tr_safety_gap"
            return False

        self.preopen_block_reason = "ready"
        return True

    def stats(self) -> dict[str, Any]:
        ensure_fields(self)
        result = original_stats(self)
        result.update(
            {
                "preopen_gap_policy": self.preopen_gap_policy,
                "preopen_strength_gap_remaining_sec": (
                    self.preopen_strength_gap_remaining_sec
                ),
                "preopen_cross_tr_gap_remaining_sec": (
                    self.preopen_cross_tr_gap_remaining_sec
                ),
                "preopen_strength_last_age_sec": self.preopen_strength_last_age_sec,
                "preopen_orderbook_last_age_sec": self.preopen_orderbook_last_age_sec,
                "preopen_opt10055_last_age_sec": self.preopen_opt10055_last_age_sec,
                "preopen_close_metrics_last_age_sec": (
                    self.preopen_close_metrics_last_age_sec
                ),
            }
        )
        return result

    scheduler_class._idle = idle
    scheduler_class.stats = stats
    scheduler_class._stockboard_gap_policy_patch_installed = True
