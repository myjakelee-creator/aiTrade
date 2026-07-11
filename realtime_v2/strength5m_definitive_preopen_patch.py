from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime
from typing import Any

PREOPEN_PHASES = {
    "after_wait",
    "aftermarket",
    "closed",
    "before_market",
    "weekend",
    "holiday",
}


def _positive(row: dict[str, Any]) -> bool:
    try:
        return float(row.get("strength_5m")) > 0
    except (TypeError, ValueError):
        return False


def _ready(provider, require_registered: bool = True) -> tuple[bool, str]:
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return False, "provider_lock_missing"
    with lock:
        check = getattr(provider, "_strength_probe_ready_state_locked", None)
        if not callable(check):
            return False, "ready_check_missing"
        result = check()
        ready, reason = (
            (bool(result[0]), str(result[1]))
            if isinstance(result, tuple)
            else (bool(result), "ready" if result else "not_ready")
        )
        if ready and require_registered:
            if not getattr(provider, "_realreg_succeeded", False):
                return False, "realreg_not_ready"
            if not getattr(provider, "_registered_codes", None):
                return False, "registered_codes_empty"
        return ready, reason


def _purge_pending(provider) -> int:
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return 0
    with lock:
        pending = getattr(provider, "_strength_probe_pending", None)
        if pending is None:
            return 0
        count = len(pending)
        pending.clear()
        codes = getattr(provider, "_strength_probe_pending_codes", None)
        if hasattr(codes, "clear"):
            codes.clear()
        return count


class OffhoursStrengthDrain:
    """Repair blank 5-minute strength values directly on the QAx owner thread."""

    def __init__(self, base, provider, scheduler_module) -> None:
        self.base = base
        self.provider = provider
        self.scheduler_module = scheduler_module
        self.url = os.getenv(
            "STOCKBOARD_V2_WORKER_SNAPSHOT_URL",
            "http://127.0.0.1:8765/api/v2/snapshot?limit=300",
        )
        self.gap_sec = max(
            1.25,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_OFFHOURS_GAP_SEC", "2.0")),
        )
        self.timeout_sec = max(
            8.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_OFFHOURS_TIMEOUT_SEC", "12")),
        )
        self.retry_sec = max(
            30.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_OFFHOURS_RETRY_SEC", "300")),
        )
        self.max_attempts = max(
            1,
            int(os.getenv("STOCKBOARD_STRENGTH_5M_OFFHOURS_MAX_ATTEMPTS", "2")),
        )
        self.refresh_sec = max(
            1.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_OFFHOURS_REFRESH_SEC", "2")),
        )

        self.mode = "waiting_provider"
        self.phase = "unknown"
        self.queue: deque[str] = deque()
        self.current: str | None = None
        self.current_started = 0.0
        self.current_requested_at: str | None = None
        self.current_trading_date: str | None = None
        self.next_request_at = 0.0
        self.last_refresh_at = 0.0
        self.attempts: dict[str, int] = {}
        self.retry_at: dict[str, float] = {}
        self.completed: set[str] = set()
        self.missing_count = 0
        self.total_count = 0
        self.enqueue_count = 0
        self.success_count = 0
        self.empty_count = 0
        self.error_count = 0
        self.timeout_count = 0
        self.purge_count = 0
        self.last_code: str | None = None
        self.last_status: str | None = None
        self.last_error: str | None = None
        self.last_tick_at: str | None = None
        self.enabled = True

    def stop(self) -> None:
        self.enabled = False
        self.mode = "stopped"

    def _refresh(self, now_mono: float) -> None:
        if now_mono - self.last_refresh_at < self.refresh_sec:
            return
        self.last_refresh_at = now_mono
        try:
            payload = self.scheduler_module._read_json_url(self.url)
            plan = self.scheduler_module.build_lane_plan(self.base, payload, "")
        except Exception as error:
            self.mode = "snapshot_error"
            self.last_error = str(error)
            return

        missing = [
            str(item.get("stock_code") or "")
            for item in plan
            if not _positive(
                item.get("row") if isinstance(item.get("row"), dict) else {}
            )
        ]
        missing = [code for code in missing if code]
        self.total_count = len(plan)
        self.missing_count = len(missing)
        if not missing:
            self.queue.clear()
            self.mode = "complete"
            return

        existing = set(self.queue)
        if self.current:
            existing.add(self.current)
        for code in missing:
            if code in existing or code in self.completed:
                continue
            if self.attempts.get(code, 0) >= self.max_attempts:
                continue
            if now_mono < self.retry_at.get(code, 0.0):
                continue
            self.queue.append(code)
            existing.add(code)

        if not self.queue and self.current is None:
            self.mode = "retry_wait"

    def _cache_result(self, code: str) -> dict[str, Any]:
        lock = getattr(self.provider, "_lock", None)
        if lock is None:
            return {}
        with lock:
            cached = getattr(self.provider, "_strength_probe_cache", {}).get(code)
            if isinstance(cached, dict):
                return dict(cached)
            last = getattr(self.provider, "_strength_probe_last_result", None)
            if isinstance(last, dict) and str(last.get("stock_code") or "") == code:
                return dict(last)
        return {}

    def _finish(self, forced_status: str | None = None) -> None:
        code = self.current
        if not code:
            return
        now_mono = time.monotonic()
        result = self._cache_result(code)
        status = str(
            forced_status
            or result.get("strength_status")
            or result.get("status")
            or "no_data"
        ).lower()
        if _positive(result) and status not in {"error", "timeout", "failed"}:
            self.success_count += 1
            self.completed.add(code)
            self.retry_at.pop(code, None)
            status = "ok"
        elif status in {"error", "timeout", "failed", "unavailable"}:
            self.error_count += 1
            if status == "timeout":
                self.timeout_count += 1
            if self.attempts.get(code, 0) < self.max_attempts:
                self.retry_at[code] = now_mono + self.retry_sec
        else:
            self.empty_count += 1
            if self.attempts.get(code, 0) < self.max_attempts:
                self.retry_at[code] = now_mono + self.retry_sec
            status = "no_data"

        self.last_code = code
        self.last_status = status
        self.current = None
        self.current_started = 0.0
        self.current_requested_at = None
        self.current_trading_date = None
        self.next_request_at = now_mono + self.gap_sec
        self.last_refresh_at = 0.0

    def _observe(self, now_mono: float) -> None:
        code = self.current
        if not code:
            return
        lock = getattr(self.provider, "_lock", None)
        if lock is None:
            self.last_error = "provider_lock_missing"
            self._finish("error")
            return
        with lock:
            inflight = getattr(self.provider, "_strength_probe_inflight", None)
            inflight_code = (
                str(inflight.get("stock_code") or "")
                if isinstance(inflight, dict)
                else ""
            )
        if inflight_code == code:
            if now_mono - self.current_started < self.timeout_sec:
                self.mode = "inflight"
                return
            with lock:
                active = getattr(self.provider, "_strength_probe_inflight", None)
                if (
                    isinstance(active, dict)
                    and str(active.get("stock_code") or "") == code
                ):
                    self.provider._strength_probe_inflight = None
            try:
                self.provider._strength_probe_error(
                    code,
                    "offhours direct drain timeout",
                    requested_at=self.current_requested_at,
                    trading_date=self.current_trading_date,
                )
            except Exception:
                pass
            self._finish("timeout")
            return
        if now_mono - self.current_started >= 0.05:
            self._finish()

    def _send(self, code: str, now_mono: float) -> None:
        ready, reason = _ready(self.provider)
        if not ready:
            self.mode = f"waiting_provider:{reason}"
            return

        provider = self.provider
        lock = provider._lock
        requested_at = datetime.now().isoformat(timespec="seconds")
        trading_date = (
            self.scheduler_module.last_completed_trading_date(datetime.now()) or None
        )
        with lock:
            control = provider._control
            if control is None:
                self.mode = "waiting_provider:control_unavailable"
                return
            provider._strength_probe_last_request_at = now_mono
            provider._strength_probe_last_by_code[code] = now_mono
            provider._strength_probe_inflight = {
                "stock_code": code,
                "trading_date": trading_date,
                "requested_at": requested_at,
                "comm_requested_at": requested_at,
                "rqname": provider._STRENGTH_PROBE_RQNAME,
                "trcode": provider._STRENGTH_PROBE_TRCODE,
                "screen_no": provider._STRENGTH_PROBE_SCREEN,
                "started_at_monotonic": now_mono,
                "owner": "offhours_direct_drain",
            }

        self.current = code
        self.current_started = now_mono
        self.current_requested_at = requested_at
        self.current_trading_date = trading_date
        self.attempts[code] = self.attempts.get(code, 0) + 1
        self.enqueue_count += 1
        self.last_code = code

        try:
            control.dynamicCall(
                "SetInputValue(QString, QString)",
                "종목코드",
                code,
            )
            result = control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                provider._STRENGTH_PROBE_RQNAME,
                provider._STRENGTH_PROBE_TRCODE,
                0,
                provider._STRENGTH_PROBE_SCREEN,
            )
            if result not in (None, 0, "0"):
                raise RuntimeError(f"CommRqData returned {result!r}")
        except Exception as error:
            with lock:
                active = getattr(provider, "_strength_probe_inflight", None)
                if (
                    isinstance(active, dict)
                    and str(active.get("stock_code") or "") == code
                ):
                    provider._strength_probe_inflight = None
            try:
                provider._strength_probe_error(
                    code,
                    str(error),
                    requested_at=requested_at,
                    trading_date=trading_date,
                )
            except Exception:
                pass
            self.last_error = str(error)
            self._finish("error")
            return

        with lock:
            active = getattr(provider, "_strength_probe_inflight", None)
            still_active = (
                isinstance(active, dict)
                and str(active.get("stock_code") or "") == code
            )
        if not still_active:
            self._finish()
            return

        requested = {
            "stock_code": code,
            "trading_date": trading_date,
            "strength_source": "opt10046_probe",
            "strength_status": "requested",
            "strength_requested_at": requested_at,
            "strength_snapshot_at": requested_at,
        }
        if provider.store is not None:
            provider.store.update_close_metrics(code, requested)
        with lock:
            provider._strength_probe_last_result = dict(requested)
        self.mode = "inflight"

    def tick(self) -> None:
        self.last_tick_at = datetime.now().isoformat(timespec="seconds")
        if not self.enabled:
            return
        session = self.scheduler_module.market_session_now()
        self.phase = str(getattr(session, "phase", "unknown") or "unknown").lower()
        if self.phase not in PREOPEN_PHASES:
            self.mode = "inactive_session"
            self.queue.clear()
            return

        self.purge_count += _purge_pending(self.provider)
        ready, reason = _ready(self.provider)
        if not ready:
            self.mode = f"waiting_provider:{reason}"
            return

        now_mono = time.monotonic()
        self._observe(now_mono)
        if self.current:
            return

        self._refresh(now_mono)
        if self.missing_count <= 0:
            self.mode = "complete"
            return
        if now_mono < self.next_request_at:
            self.mode = "rate_gap"
            return

        while self.queue:
            code = self.queue.popleft()
            if self.attempts.get(code, 0) >= self.max_attempts:
                continue
            if now_mono < self.retry_at.get(code, 0.0):
                continue
            self._send(code, now_mono)
            return
        self.mode = "retry_wait"

    def stats(self) -> dict[str, Any]:
        return {
            "controller": "offhours_direct_drain_v1",
            "mode": self.mode,
            "market_phase": self.phase,
            "missing_count": self.missing_count,
            "total_count": self.total_count,
            "queue_remaining": len(self.queue),
            "current_code": self.current,
            "enqueue_count": self.enqueue_count,
            "preopen_success_count": self.success_count,
            "preopen_empty_count": self.empty_count,
            "preopen_error_count": self.error_count,
            "timeout_count": self.timeout_count,
            "request_gap_sec": self.gap_sec,
            "hard_timeout_sec": self.timeout_sec,
            "pending_purge_count": self.purge_count,
            "last_enqueued_code": self.last_code,
            "last_result_status": self.last_status,
            "last_cycle_at": self.last_tick_at,
            "last_error": self.last_error,
        }


def install() -> None:
    """Use a direct off-hours drain; keep the normal-session scheduler unchanged."""

    from realtime_v2 import strength5m_scheduler as scheduler_module

    if getattr(scheduler_module, "_stockboard_offhours_direct_drain_installed", False):
        return

    scheduler_class = scheduler_module.Strength5mScheduler
    original_stats = scheduler_class.stats

    def preopen_cycle(self, session, plan):
        drain = getattr(self.provider, "_stockboard_offhours_strength_drain", None)
        self.preopen_mode = "offhours_direct_drain"
        if drain is not None:
            self.preopen_total_count = drain.total_count
            self.preopen_missing_count = drain.missing_count
            self.preopen_filled_count = max(
                0,
                drain.total_count - drain.missing_count,
            )

    def scheduler_stats(self):
        result = original_stats(self)
        drain = getattr(self.provider, "_stockboard_offhours_strength_drain", None)
        if drain is not None and drain.phase in PREOPEN_PHASES:
            result.update(drain.stats())
            with self.provider._lock:
                pending = getattr(self.provider, "_strength_probe_pending", ())
                inflight = getattr(self.provider, "_strength_probe_inflight", None)
                result["provider_strength_pending_count"] = len(pending)
                result["provider_strength_inflight_code"] = (
                    inflight.get("stock_code")
                    if isinstance(inflight, dict)
                    else None
                )
            result["preopen_block_reason"] = drain.mode
        return result

    scheduler_class._preopen_cycle = preopen_cycle
    scheduler_class.stats = scheduler_stats
    original_install = scheduler_module.install

    def install_with_drain(base) -> None:
        original_install(base)
        provider_class = base.KiwoomOpenApiRealtimeProvider
        if getattr(
            provider_class,
            "_stockboard_offhours_direct_drain_provider_installed",
            False,
        ):
            return

        original_register = provider_class.register_codes
        original_stop = provider_class.stop
        original_status = provider_class.status
        original_pump = provider_class.pump_inline_qt_once

        def register(provider, codes):
            result = original_register(provider, codes)
            if result and getattr(
                provider,
                "_stockboard_offhours_strength_drain",
                None,
            ) is None:
                provider._stockboard_offhours_strength_drain = OffhoursStrengthDrain(
                    base,
                    provider,
                    scheduler_module,
                )
            return result

        def stop(provider):
            drain = getattr(provider, "_stockboard_offhours_strength_drain", None)
            if drain is not None:
                drain.stop()
            return original_stop(provider)

        def status(provider):
            result = original_status(provider)
            result = result if isinstance(result, dict) else {"status": result}
            drain = getattr(provider, "_stockboard_offhours_strength_drain", None)
            if drain is not None:
                result["strength5m_offhours_drain"] = drain.stats()
            return result

        def pump(provider):
            ok = original_pump(provider)
            drain = getattr(provider, "_stockboard_offhours_strength_drain", None)
            if drain is not None:
                drain.tick()
            return ok

        provider_class.register_codes = register
        provider_class.stop = stop
        provider_class.status = status
        provider_class.pump_inline_qt_once = pump
        provider_class._stockboard_offhours_direct_drain_provider_installed = True

    scheduler_module.install = install_with_drain
    scheduler_module._stockboard_offhours_direct_drain_installed = True
