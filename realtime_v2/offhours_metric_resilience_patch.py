from __future__ import annotations

import time
from datetime import datetime
from typing import Any


def install() -> None:
    """Keep the off-hours completion loop moving after a bad row or stale gap."""

    from realtime_v2 import strength5m_definitive_preopen_patch as definitive

    drain_class = definitive.OffhoursStrengthDrain
    if getattr(drain_class, "_stockboard_metric_resilience_installed", False):
        return

    original_tick = drain_class.tick
    original_stats = drain_class.stats

    def ensure_fields(self) -> None:
        defaults = {
            "tick_count": 0,
            "tick_error_count": 0,
            "tick_last_error": None,
            "last_tick_monotonic": 0.0,
            "rate_gap_started_monotonic": 0.0,
            "rate_gap_remaining_sec": None,
            "rate_gap_recovery_count": 0,
        }
        for name, value in defaults.items():
            if not hasattr(self, name):
                setattr(self, name, value)

    def clear_current_after_error(self, error: Exception, now_mono: float) -> None:
        ensure_fields(self)
        self.tick_error_count += 1
        self.tick_last_error = f"{type(error).__name__}: {error}"
        self.last_error = f"tick recovered: {self.tick_last_error}"

        task = getattr(self, "current", None)
        if isinstance(task, tuple) and len(task) == 2:
            kind, code = str(task[0]), str(task[1])
            try:
                self._clear_inflight(kind, code)
            except Exception:
                pass
            try:
                self._record_error(
                    kind,
                    code,
                    f"offhours loop recovered: {self.tick_last_error}",
                )
            except Exception:
                pass
            try:
                attempt = max(1, int(getattr(self, "attempts", {}).get(task, 1) or 1))
                retry_delay = self._retry_delay(attempt)
                self.retry_at[task] = now_mono + retry_delay
            except Exception:
                pass
            self.last_code = code or getattr(self, "last_code", None)
            self.last_kind = kind or getattr(self, "last_kind", None)
            self.last_status = "tick_error"
        elif isinstance(task, str) and task:
            code = task
            try:
                lock = getattr(self.provider, "_lock", None)
                if lock is not None:
                    with lock:
                        inflight = getattr(self.provider, "_strength_probe_inflight", None)
                        if isinstance(inflight, dict) and str(inflight.get("stock_code") or "") == code:
                            self.provider._strength_probe_inflight = None
            except Exception:
                pass
            self.last_code = code
            self.last_status = "tick_error"

        self.current = None
        self.current_started = 0.0
        if hasattr(self, "current_requested_at"):
            self.current_requested_at = None
        if hasattr(self, "current_trading_date"):
            self.current_trading_date = None
        if hasattr(self, "current_needs_strength5"):
            self.current_needs_strength5 = False
        if hasattr(self, "current_needs_execution"):
            self.current_needs_execution = False
        self.next_request_at = 0.0
        self.last_refresh_at = 0.0
        self.mode = "tick_error_recovered"

    def tick(self) -> None:
        ensure_fields(self)
        now_mono = time.monotonic()
        self.tick_count += 1
        self.last_tick_monotonic = now_mono

        # A valid rate gap is short.  If it survives longer than the configured
        # gap plus one second, release it so reconnecting is never required.
        if (
            getattr(self, "mode", None) == "rate_gap"
            and getattr(self, "current", None) is None
            and len(getattr(self, "queue", ())) > 0
        ):
            if not self.rate_gap_started_monotonic:
                self.rate_gap_started_monotonic = now_mono
            gap_sec = max(0.1, float(getattr(self, "gap_sec", 2.0) or 2.0))
            remaining = float(getattr(self, "next_request_at", 0.0) or 0.0) - now_mono
            self.rate_gap_remaining_sec = round(max(0.0, remaining), 3)
            if (
                now_mono - self.rate_gap_started_monotonic > gap_sec + 1.0
                or remaining > gap_sec + 1.0
            ):
                self.next_request_at = 0.0
                self.rate_gap_started_monotonic = 0.0
                self.rate_gap_remaining_sec = 0.0
                self.rate_gap_recovery_count += 1
                self.mode = "rate_gap_recovered"
        else:
            self.rate_gap_started_monotonic = 0.0
            self.rate_gap_remaining_sec = None

        try:
            original_tick(self)
        except Exception as error:
            clear_current_after_error(self, error, now_mono)
            return

        if getattr(self, "mode", None) == "rate_gap":
            if not self.rate_gap_started_monotonic:
                self.rate_gap_started_monotonic = now_mono
            remaining = float(getattr(self, "next_request_at", 0.0) or 0.0) - time.monotonic()
            self.rate_gap_remaining_sec = round(max(0.0, remaining), 3)
        else:
            self.rate_gap_started_monotonic = 0.0
            self.rate_gap_remaining_sec = None

    def stats(self) -> dict[str, Any]:
        ensure_fields(self)
        result = original_stats(self)
        now_mono = time.monotonic()
        last_tick = float(getattr(self, "last_tick_monotonic", 0.0) or 0.0)
        result.update(
            {
                "tick_count": int(self.tick_count),
                "tick_error_count": int(self.tick_error_count),
                "tick_last_error": self.tick_last_error,
                "last_tick_age_sec": (
                    round(max(0.0, now_mono - last_tick), 3)
                    if last_tick > 0
                    else None
                ),
                "rate_gap_remaining_sec": self.rate_gap_remaining_sec,
                "rate_gap_recovery_count": int(self.rate_gap_recovery_count),
                "resilience": "offhours_metric_fail_open_v1",
                "resilience_status_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        return result

    drain_class.tick = tick
    drain_class.stats = stats
    drain_class._stockboard_metric_resilience_installed = True
