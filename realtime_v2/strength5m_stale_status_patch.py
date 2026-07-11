from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any


_PENDING_STATUSES = {"pending", "requested", "deferred"}


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
    if getattr(scheduler_class, "_stockboard_stale_status_patch_installed", False):
        return

    original_idle = scheduler_class._idle
    original_stats = scheduler_class.stats
    pending_timeout_sec = max(
        15.0,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_PENDING_WATCHDOG_SEC", "30")),
    )
    inflight_grace_sec = max(
        1.0,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_INFLIGHT_GRACE_SEC", "3")),
    )

    def _ensure_runtime_fields(self) -> None:
        if not hasattr(self, "preopen_stale_status_released"):
            self.preopen_stale_status_released = set()
        if not hasattr(self, "preopen_watchdog_release_count"):
            self.preopen_watchdog_release_count = 0
        if not hasattr(self, "preopen_watchdog_last_code"):
            self.preopen_watchdog_last_code = None
        if not hasattr(self, "preopen_watchdog_last_kind"):
            self.preopen_watchdog_last_kind = None
        if not hasattr(self, "preopen_block_reason"):
            self.preopen_block_reason = None
        if not hasattr(self, "preopen_ignored_close_metrics_queue_count"):
            self.preopen_ignored_close_metrics_queue_count = 0

    def _mark_watchdog_release(self, code: str, kind: str, now_mono: float) -> None:
        _ensure_runtime_fields(self)
        self.preopen_watchdog_release_count += 1
        self.preopen_watchdog_last_code = code or None
        self.preopen_watchdog_last_kind = kind
        self.preopen_requested_at.pop(code, None)
        attempt = max(1, int(self.preopen_attempts.get(code, 1) or 1))
        retry_delay = self._retry_delay(attempt)
        self.preopen_retry_at[code] = max(
            float(self.preopen_retry_at.get(code, 0.0) or 0.0),
            now_mono + retry_delay,
        )

    def _release_stuck_strength_request(self, now_mono: float) -> bool:
        provider = self.provider
        lock = getattr(provider, "_lock", None)
        if lock is None:
            return False

        released: tuple[str, dict[str, Any]] | None = None
        with lock:
            inflight = getattr(provider, "_strength_probe_inflight", None)
            if isinstance(inflight, dict):
                started_at = inflight.get("started_at_monotonic")
                try:
                    inflight_age = now_mono - float(started_at)
                except (TypeError, ValueError):
                    inflight_age = None
                provider_timeout = max(
                    1.0,
                    float(getattr(provider, "_strength_probe_timeout_sec", 10.0) or 10.0),
                )
                if inflight_age is not None and inflight_age >= provider_timeout + inflight_grace_sec:
                    provider._strength_probe_inflight = None
                    released = ("inflight", dict(inflight))

            if released is None:
                pending = getattr(provider, "_strength_probe_pending", None)
                if pending:
                    item = pending[0]
                    code = str(item.get("stock_code") or "") if isinstance(item, dict) else ""
                    pending_age = _iso_age_sec(item.get("requested_at")) if isinstance(item, dict) else None
                    requested_mono = self.preopen_requested_at.get(code)
                    if requested_mono is not None:
                        local_age = max(0.0, now_mono - float(requested_mono))
                        pending_age = local_age if pending_age is None else max(pending_age, local_age)
                    if pending_age is not None and pending_age >= pending_timeout_sec:
                        popped = pending.popleft()
                        pending_codes = getattr(provider, "_strength_probe_pending_codes", None)
                        if hasattr(pending_codes, "discard"):
                            pending_codes.discard(code)
                        released = ("pending", dict(popped) if isinstance(popped, dict) else {"stock_code": code})

        if released is None:
            return False

        kind, item = released
        code = str(item.get("stock_code") or "")
        requested_at = item.get("requested_at")
        trading_date = item.get("trading_date")
        try:
            provider._strength_probe_error(
                code,
                f"preopen watchdog released stuck {kind}",
                requested_at=requested_at,
                trading_date=trading_date,
            )
        except Exception as error:
            try:
                provider._strength_probe_last_error = f"preopen watchdog: {error}"
            except Exception:
                pass
        _mark_watchdog_release(self, code, kind, now_mono)
        return True

    def preopen_candidate_due(
        self,
        item: dict[str, Any],
        *,
        final_window: bool,
        now_mono: float | None = None,
    ) -> tuple[bool, float]:
        _ensure_runtime_fields(self)
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
            self.preopen_stale_status_released.add(code)

        if final_window and code not in self.preopen_final_attempted:
            return True, float("inf")

        due_at = float(self.preopen_retry_at.get(code, 0.0) or 0.0)
        if now_mono < due_at:
            return False, due_at - now_mono

        attempt = int(self.preopen_attempts.get(code, 0) or 0)
        return True, float("inf") if attempt == 0 else float(attempt)

    def idle(self, session=None, gap_override: float | None = None) -> bool:
        _ensure_runtime_fields(self)
        if not self._preopen_window(session):
            self.preopen_block_reason = None
            return original_idle(self, session, gap_override=gap_override)

        now_mono = time.monotonic()
        _release_stuck_strength_request(self, now_mono)

        provider = self.provider
        lock = getattr(provider, "_lock", None)
        if lock is None:
            self.preopen_block_reason = "provider_lock_missing"
            return False

        with lock:
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

            # Legacy close-metrics work is a non-COM placeholder queue. It can be
            # very large after a restart and previously starved opt10046 forever.
            close_metrics_queue = getattr(provider, "_close_metrics_queue", ())
            self.preopen_ignored_close_metrics_queue_count = len(close_metrics_queue)

            last_request = max(
                float(getattr(provider, name, 0.0) or 0.0)
                for name in (
                    "_strength_probe_last_request_at",
                    "_orderbook_probe_last_request_at",
                    "_opt10055_probe_last_request_at",
                    "_close_metrics_last_request_at",
                )
            )

        required_gap = self.gap(session) if gap_override is None else max(
            self.normal_gap,
            float(gap_override),
        )
        remaining = required_gap - (now_mono - last_request)
        if remaining > 0:
            self.preopen_block_reason = "global_request_gap"
            return False

        self.preopen_block_reason = "ready"
        return True

    def stats(self) -> dict[str, Any]:
        _ensure_runtime_fields(self)
        result = original_stats(self)
        provider = self.provider
        lock = getattr(provider, "_lock", None)
        provider_status: dict[str, Any] = {}
        if lock is not None:
            with lock:
                strength_inflight = getattr(provider, "_strength_probe_inflight", None)
                orderbook_inflight = getattr(provider, "_orderbook_probe_inflight", None)
                opt10055_inflight = getattr(provider, "_opt10055_probe_inflight", None)
                provider_status = {
                    "provider_strength_pending_count": len(getattr(provider, "_strength_probe_pending", ())),
                    "provider_strength_inflight_code": (
                        strength_inflight.get("stock_code") if isinstance(strength_inflight, dict) else None
                    ),
                    "provider_orderbook_pending_count": len(getattr(provider, "_orderbook_probe_pending", ())),
                    "provider_orderbook_inflight_code": (
                        orderbook_inflight.get("stock_code") if isinstance(orderbook_inflight, dict) else None
                    ),
                    "provider_opt10055_pending_count": len(getattr(provider, "_opt10055_probe_pending", ())),
                    "provider_opt10055_inflight_code": (
                        opt10055_inflight.get("stock_code") if isinstance(opt10055_inflight, dict) else None
                    ),
                    "provider_close_metrics_queue_count": len(getattr(provider, "_close_metrics_queue", ())),
                }
        result.update(
            {
                "stale_pending_status_release_count": len(self.preopen_stale_status_released),
                "stale_pending_status_release_sample": sorted(self.preopen_stale_status_released)[:20],
                "preopen_block_reason": self.preopen_block_reason,
                "preopen_watchdog_release_count": self.preopen_watchdog_release_count,
                "preopen_watchdog_last_code": self.preopen_watchdog_last_code,
                "preopen_watchdog_last_kind": self.preopen_watchdog_last_kind,
                "preopen_ignored_close_metrics_queue_count": self.preopen_ignored_close_metrics_queue_count,
                **provider_status,
            }
        )
        return result

    scheduler_class._preopen_candidate_due = preopen_candidate_due
    scheduler_class._idle = idle
    scheduler_class.stats = stats
    scheduler_class._stockboard_stale_status_patch_installed = True
