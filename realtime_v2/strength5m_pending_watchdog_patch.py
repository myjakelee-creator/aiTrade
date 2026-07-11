from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any


def _iso_age_sec(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return max(0.0, (datetime.now() - parsed).total_seconds())


def install() -> None:
    from realtime_v2 import strength5m_scheduler as scheduler_module

    scheduler_class = scheduler_module.Strength5mScheduler
    if getattr(scheduler_class, "_stockboard_pending_watchdog_patch_installed", False):
        return

    original_idle = scheduler_class._idle
    original_stats = scheduler_class.stats
    timeout_sec = max(
        10.0,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_ORPHAN_PENDING_SEC", "15")),
    )

    def ensure_fields(self) -> None:
        if not hasattr(self, "preopen_orphan_pending_seen"):
            self.preopen_orphan_pending_seen: dict[str, float] = {}
        if not hasattr(self, "preopen_pending_nudge_count"):
            self.preopen_pending_nudge_count = 0
        if not hasattr(self, "preopen_pending_nudge_last_code"):
            self.preopen_pending_nudge_last_code = None

    def provider_ready_locked(provider) -> bool:
        check = getattr(provider, "_strength_probe_ready_state_locked", None)
        if not callable(check):
            return False
        try:
            result = check()
        except Exception:
            return False
        if isinstance(result, tuple):
            return bool(result[0])
        return bool(result)

    def nudge_or_release_orphan_pending(self, now_mono: float) -> bool:
        ensure_fields(self)
        provider = self.provider
        lock = getattr(provider, "_lock", None)
        if lock is None:
            return False

        released: dict[str, Any] | None = None
        with lock:
            if getattr(provider, "_strength_probe_inflight", None):
                return False

            pending = getattr(provider, "_strength_probe_pending", None)
            if not pending:
                self.preopen_orphan_pending_seen.clear()
                return False

            item = pending[0]
            if not isinstance(item, dict):
                return False
            code = str(item.get("stock_code") or "")
            if not code:
                return False

            for stale_code in list(self.preopen_orphan_pending_seen):
                if stale_code != code:
                    self.preopen_orphan_pending_seen.pop(stale_code, None)
            first_seen = self.preopen_orphan_pending_seen.setdefault(code, now_mono)

            if provider_ready_locked(provider):
                try:
                    retry_at = float(item.get("next_retry_at_monotonic") or 0.0)
                except (TypeError, ValueError):
                    retry_at = 0.0
                if retry_at > now_mono or "next_retry_at_monotonic" not in item:
                    item["next_retry_at_monotonic"] = now_mono
                    self.preopen_pending_nudge_count += 1
                    self.preopen_pending_nudge_last_code = code

            ages = [max(0.0, now_mono - first_seen)]
            wall_age = _iso_age_sec(item.get("requested_at"))
            if wall_age is not None:
                ages.append(wall_age)
            scheduler_requested = getattr(self, "preopen_requested_at", {}).get(code)
            if scheduler_requested is not None:
                try:
                    ages.append(max(0.0, now_mono - float(scheduler_requested)))
                except (TypeError, ValueError):
                    pass

            if max(ages) < timeout_sec:
                return False

            popped = pending.popleft()
            pending_codes = getattr(provider, "_strength_probe_pending_codes", None)
            if hasattr(pending_codes, "discard"):
                pending_codes.discard(code)
            self.preopen_orphan_pending_seen.pop(code, None)
            released = dict(popped)

        if released is None:
            return False

        code = str(released.get("stock_code") or "")
        requested_at = released.get("requested_at")
        trading_date = released.get("trading_date")
        try:
            provider._strength_probe_error(
                code,
                "preopen pending watchdog released orphan queue head",
                requested_at=requested_at,
                trading_date=trading_date,
            )
        except Exception as error:
            try:
                provider._strength_probe_last_error = f"preopen pending watchdog: {error}"
            except Exception:
                pass

        self.preopen_requested_at.pop(code, None)
        attempt = max(1, int(self.preopen_attempts.get(code, 1) or 1))
        self.preopen_retry_at[code] = now_mono + self._retry_delay(attempt)
        self.preopen_watchdog_release_count = int(
            getattr(self, "preopen_watchdog_release_count", 0) or 0
        ) + 1
        self.preopen_watchdog_last_code = code or None
        self.preopen_watchdog_last_kind = "pending_orphan"
        return True

    def idle(self, session=None, gap_override: float | None = None) -> bool:
        ensure_fields(self)
        if self._preopen_window(session):
            nudge_or_release_orphan_pending(self, time.monotonic())
        return original_idle(self, session, gap_override=gap_override)

    def stats(self) -> dict[str, Any]:
        ensure_fields(self)
        result = original_stats(self)
        result.update(
            {
                "preopen_orphan_pending_timeout_sec": timeout_sec,
                "preopen_orphan_pending_seen_count": len(
                    self.preopen_orphan_pending_seen
                ),
                "preopen_orphan_pending_seen_sample": sorted(
                    self.preopen_orphan_pending_seen
                )[:20],
                "preopen_pending_nudge_count": self.preopen_pending_nudge_count,
                "preopen_pending_nudge_last_code": self.preopen_pending_nudge_last_code,
            }
        )
        return result

    scheduler_class._idle = idle
    scheduler_class.stats = stats
    scheduler_class._stockboard_pending_watchdog_patch_installed = True
