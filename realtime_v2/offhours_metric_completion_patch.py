from __future__ import annotations

import os
import time
from collections import deque
from datetime import datetime
from typing import Any

OFFHOURS_PHASES = {"closed", "before_market", "weekend", "holiday"}
RETRY_DELAYS_SEC = (300.0, 1800.0, 7200.0)


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _first_positive(source: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _positive(source.get(key))
        if value is not None:
            return value
    return None


def _ready(provider) -> tuple[bool, str]:
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return False, "provider_lock_missing"
    with lock:
        check = getattr(provider, "_strength_probe_ready_state_locked", None)
        if not callable(check):
            return False, "ready_check_missing"
        try:
            result = check()
        except Exception as error:
            return False, f"ready_check_failed:{error}"
        if isinstance(result, tuple):
            ready, reason = bool(result[0]), str(result[1] or "not_ready")
        else:
            ready, reason = bool(result), "ready" if result else "not_ready"
        if ready and not bool(getattr(provider, "_realreg_succeeded", False)):
            return False, "realreg_not_ready"
        if ready and not getattr(provider, "_registered_codes", None):
            return False, "registered_codes_empty"
        return ready, reason


def _purge_queue(provider, pending_name: str, code_set_name: str) -> int:
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return 0
    with lock:
        pending = getattr(provider, pending_name, None)
        if pending is None:
            return 0
        count = len(pending)
        pending.clear()
        codes = getattr(provider, code_set_name, None)
        if hasattr(codes, "clear"):
            codes.clear()
        return count


class OffhoursMetricCompletionDrain:
    """Fill strength, instant strength, and bid/ask blanks off-hours.

    Requests are issued directly from the QAx owner thread.  No provider pending
    queue is used, so one stale queue entry cannot stop the whole completion pass.
    """

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
            float(os.getenv("STOCKBOARD_OFFHOURS_METRIC_GAP_SEC", "2.0")),
        )
        self.timeout_sec = max(
            8.0,
            float(os.getenv("STOCKBOARD_OFFHOURS_METRIC_TIMEOUT_SEC", "12")),
        )
        self.refresh_sec = max(
            1.0,
            float(os.getenv("STOCKBOARD_OFFHOURS_METRIC_REFRESH_SEC", "2")),
        )

        self.enabled = True
        self.mode = "waiting_provider"
        self.phase = "unknown"
        self.queue: deque[tuple[str, str]] = deque()
        self.current: tuple[str, str] | None = None
        self.current_started = 0.0
        self.current_requested_at: str | None = None
        self.current_trading_date: str | None = None
        self.current_needs_strength5 = False
        self.current_needs_execution = False
        self.next_request_at = 0.0
        self.last_refresh_at = 0.0

        self.attempts: dict[tuple[str, str], int] = {}
        self.retry_at: dict[tuple[str, str], float] = {}
        self.settle_until: dict[tuple[str, str], float] = {}

        self.total_count = 0
        self.missing_count = 0
        self.missing_strength5_count = 0
        self.missing_execution_count = 0
        self.missing_orderbook_count = 0
        self.request_count = 0
        self.strength_request_count = 0
        self.orderbook_request_count = 0
        self.success_count = 0
        self.strength_success_count = 0
        self.orderbook_success_count = 0
        self.partial_count = 0
        self.empty_count = 0
        self.error_count = 0
        self.timeout_count = 0
        self.pending_purge_count = 0
        self.last_code: str | None = None
        self.last_kind: str | None = None
        self.last_status: str | None = None
        self.last_error: str | None = None
        self.last_tick_at: str | None = None

    def stop(self) -> None:
        self.enabled = False
        self.mode = "stopped"

    def _retry_delay(self, attempt: int) -> float:
        index = max(0, int(attempt) - 1)
        if index >= len(RETRY_DELAYS_SEC):
            return RETRY_DELAYS_SEC[-1]
        return RETRY_DELAYS_SEC[index]

    def _row_needs(self, row: dict[str, Any]) -> tuple[bool, bool, bool]:
        strength5 = _first_positive(
            row,
            ("strength_5m", "last_valid_strength_5m"),
        )
        execution = _first_positive(
            row,
            (
                "execution_strength",
                "last_valid_execution_strength",
                "realtime_strength_snapshot",
            ),
        )
        bidask = _first_positive(
            row,
            (
                "bid_ask_ratio",
                "last_valid_bid_ask_ratio",
                "regular_close_bid_ask_ratio",
            ),
        )
        return strength5 is None, execution is None, bidask is None

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

        strength_tasks: list[tuple[str, str]] = []
        orderbook_tasks: list[tuple[str, str]] = []
        target_keys: set[tuple[str, str]] = set()
        strength5_missing = execution_missing = orderbook_missing = 0

        for item in plan:
            code = str(item.get("stock_code") or "")
            row = item.get("row") if isinstance(item.get("row"), dict) else {}
            if not code:
                continue
            need_strength5, need_execution, need_orderbook = self._row_needs(row)
            if need_strength5:
                strength5_missing += 1
            if need_execution:
                execution_missing += 1
            if need_orderbook:
                orderbook_missing += 1
            if need_strength5 or need_execution:
                task = ("strength", code)
                strength_tasks.append(task)
                target_keys.add(task)
            if need_orderbook:
                task = ("orderbook", code)
                orderbook_tasks.append(task)
                target_keys.add(task)

        self.total_count = len(plan)
        self.missing_strength5_count = strength5_missing
        self.missing_execution_count = execution_missing
        self.missing_orderbook_count = orderbook_missing
        # Keep backward-compatible meaning: missing_count is the 5-minute blank count.
        self.missing_count = strength5_missing

        for key in list(self.attempts):
            if key not in target_keys:
                self.attempts.pop(key, None)
                self.retry_at.pop(key, None)
                self.settle_until.pop(key, None)

        if not target_keys:
            self.queue.clear()
            self.mode = "complete"
            return

        queued = set(self.queue)
        if self.current is not None:
            queued.add(self.current)
        for task in [*strength_tasks, *orderbook_tasks]:
            if task in queued:
                continue
            if now_mono < self.retry_at.get(task, 0.0):
                continue
            if now_mono < self.settle_until.get(task, 0.0):
                continue
            self.queue.append(task)
            queued.add(task)

        if not self.queue and self.current is None:
            self.mode = "retry_wait"

    def _cache_result(self, kind: str, code: str) -> dict[str, Any]:
        lock = getattr(self.provider, "_lock", None)
        if lock is None:
            return {}
        with lock:
            if kind == "strength":
                cached = getattr(self.provider, "_strength_probe_cache", {}).get(code)
                last = getattr(self.provider, "_strength_probe_last_result", None)
            else:
                cached = getattr(self.provider, "_orderbook_probe_cache", {}).get(code)
                last = getattr(self.provider, "_orderbook_probe_last_result", None)
            if isinstance(cached, dict):
                return dict(cached)
            if isinstance(last, dict) and str(last.get("stock_code") or "") == code:
                return dict(last)
        return {}

    def _publish_execution_strength(self, code: str, result: dict[str, Any]) -> float | None:
        value = _first_positive(
            result,
            ("execution_strength", "realtime_strength_snapshot"),
        )
        if value is None:
            return None
        snapshot_at = (
            result.get("strength_completed_at")
            or result.get("strength_snapshot_at")
            or datetime.now().isoformat(timespec="seconds")
        )
        metrics = {
            "execution_strength": round(value, 4),
            "last_valid_execution_strength": round(value, 4),
            "execution_strength_updated_at": snapshot_at,
            "last_valid_strength_at": snapshot_at,
            "execution_strength_source": "opt10046_offhours",
        }
        store = getattr(self.provider, "store", None)
        if store is not None:
            store.update_close_metrics(code, metrics)
        return value

    def _finish(self, forced_status: str | None = None) -> None:
        task = self.current
        if task is None:
            return
        kind, code = task
        now_mono = time.monotonic()
        result = self._cache_result(kind, code)
        status = str(
            forced_status
            or result.get("strength_status")
            or result.get("orderbook_status")
            or result.get("status")
            or "no_data"
        ).strip().lower()

        ok = False
        partial = False
        if kind == "strength":
            strength5 = _first_positive(result, ("strength_5m",))
            execution = self._publish_execution_strength(code, result)
            strength_ok = (not self.current_needs_strength5) or strength5 is not None
            execution_ok = (not self.current_needs_execution) or execution is not None
            ok = strength_ok and execution_ok
            partial = (strength5 is not None or execution is not None) and not ok
        else:
            ratio = _first_positive(
                result,
                ("bid_ask_ratio", "bid_ask_ratio_snapshot"),
            )
            ok = ratio is not None

        if ok:
            self.success_count += 1
            if kind == "strength":
                self.strength_success_count += 1
            else:
                self.orderbook_success_count += 1
            self.attempts.pop(task, None)
            self.retry_at.pop(task, None)
            self.settle_until[task] = now_mono + max(5.0, self.refresh_sec * 2)
            status = "ok"
        else:
            if partial:
                self.partial_count += 1
                status = "partial"
            elif status in {"error", "timeout", "failed", "unavailable"}:
                self.error_count += 1
                if status == "timeout":
                    self.timeout_count += 1
            else:
                self.empty_count += 1
                status = "no_data"
            attempt = max(1, int(self.attempts.get(task, 1) or 1))
            self.retry_at[task] = now_mono + self._retry_delay(attempt)

        self.last_code = code
        self.last_kind = kind
        self.last_status = status
        self.current = None
        self.current_started = 0.0
        self.current_requested_at = None
        self.current_trading_date = None
        self.current_needs_strength5 = False
        self.current_needs_execution = False
        self.next_request_at = now_mono + self.gap_sec
        self.last_refresh_at = 0.0

    def _inflight(self, kind: str) -> dict[str, Any] | None:
        lock = getattr(self.provider, "_lock", None)
        if lock is None:
            return None
        name = "_strength_probe_inflight" if kind == "strength" else "_orderbook_probe_inflight"
        with lock:
            value = getattr(self.provider, name, None)
            return dict(value) if isinstance(value, dict) else None

    def _clear_inflight(self, kind: str, code: str) -> None:
        lock = getattr(self.provider, "_lock", None)
        if lock is None:
            return
        name = "_strength_probe_inflight" if kind == "strength" else "_orderbook_probe_inflight"
        with lock:
            active = getattr(self.provider, name, None)
            if isinstance(active, dict) and str(active.get("stock_code") or "") == code:
                setattr(self.provider, name, None)

    def _record_error(self, kind: str, code: str, message: str, status: str = "error") -> None:
        try:
            if kind == "strength":
                self.provider._strength_probe_error(
                    code,
                    message,
                    requested_at=self.current_requested_at,
                    trading_date=self.current_trading_date,
                )
            else:
                self.provider._orderbook_probe_error(
                    code,
                    message,
                    requested_at=self.current_requested_at,
                    status=status,
                )
        except Exception:
            pass

    def _observe(self, now_mono: float) -> None:
        task = self.current
        if task is None:
            return
        kind, code = task
        inflight = self._inflight(kind)
        if inflight is not None and str(inflight.get("stock_code") or "") == code:
            if now_mono - self.current_started < self.timeout_sec:
                self.mode = f"{kind}_inflight"
                return
            self._clear_inflight(kind, code)
            self._record_error(kind, code, "offhours metric completion timeout", status="timeout")
            self._finish("timeout")
            return
        if now_mono - self.current_started >= 0.05:
            self._finish()

    def _other_tr_busy(self) -> str | None:
        lock = getattr(self.provider, "_lock", None)
        if lock is None:
            return "provider_lock_missing"
        with lock:
            for reason, value in (
                ("strength_inflight", getattr(self.provider, "_strength_probe_inflight", None)),
                ("orderbook_inflight", getattr(self.provider, "_orderbook_probe_inflight", None)),
                ("opt10055_inflight", getattr(self.provider, "_opt10055_probe_inflight", None)),
            ):
                if value:
                    return reason
            if len(getattr(self.provider, "_opt10055_probe_pending", ())):
                return "opt10055_pending"
        return None

    def _send(self, task: tuple[str, str], row: dict[str, Any], now_mono: float) -> None:
        kind, code = task
        ready, reason = _ready(self.provider)
        if not ready:
            self.mode = f"waiting_provider:{reason}"
            return
        busy = self._other_tr_busy()
        if busy:
            self.mode = f"waiting_other_tr:{busy}"
            return

        provider = self.provider
        lock = provider._lock
        requested_at = datetime.now().isoformat(timespec="seconds")
        trading_date = self.scheduler_module.last_completed_trading_date(datetime.now()) or None
        need_strength5, need_execution, _need_orderbook = self._row_needs(row)

        if kind == "strength":
            rqname = provider._STRENGTH_PROBE_RQNAME
            trcode = provider._STRENGTH_PROBE_TRCODE
            screen = provider._STRENGTH_PROBE_SCREEN
            inflight_name = "_strength_probe_inflight"
            last_name = "_strength_probe_last_request_at"
        else:
            rqname = provider._ORDERBOOK_PROBE_RQNAME
            trcode = provider._ORDERBOOK_PROBE_TRCODE
            screen = provider._ORDERBOOK_PROBE_SCREEN
            inflight_name = "_orderbook_probe_inflight"
            last_name = "_orderbook_probe_last_request_at"

        with lock:
            control = provider._control
            if control is None:
                self.mode = "waiting_provider:control_unavailable"
                return
            setattr(provider, last_name, now_mono)
            last_by_code_name = (
                "_strength_probe_last_by_code"
                if kind == "strength"
                else "_orderbook_probe_last_by_code"
            )
            getattr(provider, last_by_code_name)[code] = now_mono
            setattr(
                provider,
                inflight_name,
                {
                    "stock_code": code,
                    "trading_date": trading_date,
                    "requested_at": requested_at,
                    "comm_requested_at": requested_at,
                    "rqname": rqname,
                    "trcode": trcode,
                    "screen_no": screen,
                    "started_at_monotonic": now_mono,
                    "owner": "offhours_metric_completion",
                },
            )

        self.current = task
        self.current_started = now_mono
        self.current_requested_at = requested_at
        self.current_trading_date = trading_date
        self.current_needs_strength5 = need_strength5 if kind == "strength" else False
        self.current_needs_execution = need_execution if kind == "strength" else False
        self.attempts[task] = self.attempts.get(task, 0) + 1
        self.request_count += 1
        if kind == "strength":
            self.strength_request_count += 1
        else:
            self.orderbook_request_count += 1
        self.last_code = code
        self.last_kind = kind

        try:
            control.dynamicCall("SetInputValue(QString, QString)", "종목코드", code)
            result = control.dynamicCall(
                "CommRqData(QString, QString, int, QString)",
                rqname,
                trcode,
                0,
                screen,
            )
            if result not in (None, 0, "0"):
                raise RuntimeError(f"CommRqData returned {result!r}")
        except Exception as error:
            self._clear_inflight(kind, code)
            self._record_error(kind, code, str(error))
            self.last_error = str(error)
            self._finish("error")
            return

        if self._inflight(kind) is None:
            self._finish()
            return

        requested = {
            "stock_code": code,
            "trading_date": trading_date,
            "strength_source" if kind == "strength" else "orderbook_source": (
                "opt10046_probe" if kind == "strength" else "opt10004_probe"
            ),
            "strength_status" if kind == "strength" else "orderbook_status": "requested",
            "strength_requested_at" if kind == "strength" else "orderbook_requested_at": requested_at,
            "strength_snapshot_at" if kind == "strength" else "orderbook_snapshot_at": requested_at,
        }
        store = getattr(provider, "store", None)
        if store is not None:
            store.update_close_metrics(code, requested)
        with lock:
            if kind == "strength":
                provider._strength_probe_last_result = dict(requested)
            else:
                provider._orderbook_probe_last_result = dict(requested)
        self.mode = f"{kind}_inflight"

    def tick(self) -> None:
        self.last_tick_at = datetime.now().isoformat(timespec="seconds")
        if not self.enabled:
            return
        session = self.scheduler_module.market_session_now()
        self.phase = str(getattr(session, "phase", "unknown") or "unknown").lower()
        if self.phase not in OFFHOURS_PHASES:
            self.mode = "inactive_session"
            self.queue.clear()
            return

        self.pending_purge_count += _purge_queue(
            self.provider,
            "_strength_probe_pending",
            "_strength_probe_pending_codes",
        )
        self.pending_purge_count += _purge_queue(
            self.provider,
            "_orderbook_probe_pending",
            "_orderbook_probe_pending_codes",
        )

        ready, reason = _ready(self.provider)
        if not ready:
            self.mode = f"waiting_provider:{reason}"
            return

        now_mono = time.monotonic()
        self._observe(now_mono)
        if self.current is not None:
            return

        self._refresh(now_mono)
        if (
            self.missing_strength5_count <= 0
            and self.missing_execution_count <= 0
            and self.missing_orderbook_count <= 0
        ):
            self.mode = "complete"
            return
        if now_mono < self.next_request_at:
            self.mode = "rate_gap"
            return

        while self.queue:
            task = self.queue.popleft()
            if now_mono < self.retry_at.get(task, 0.0):
                continue
            kind, code = task
            try:
                payload = self.scheduler_module._read_json_url(self.url)
                plan = self.scheduler_module.build_lane_plan(self.base, payload, "")
                row = next(
                    (
                        item.get("row")
                        for item in plan
                        if str(item.get("stock_code") or "") == code
                        and isinstance(item.get("row"), dict)
                    ),
                    {},
                )
            except Exception as error:
                self.mode = "snapshot_error"
                self.last_error = str(error)
                return
            self._send(task, row, now_mono)
            return

        self.mode = "retry_wait"

    def stats(self) -> dict[str, Any]:
        current_kind = self.current[0] if self.current else None
        current_code = self.current[1] if self.current else None
        now_mono = time.monotonic()
        retry_wait_count = sum(1 for value in self.retry_at.values() if value > now_mono)
        return {
            "controller": "offhours_metric_completion_v1",
            "mode": self.mode,
            "market_phase": self.phase,
            "missing_count": self.missing_count,
            "missing_strength5_count": self.missing_strength5_count,
            "missing_execution_count": self.missing_execution_count,
            "missing_orderbook_count": self.missing_orderbook_count,
            "total_count": self.total_count,
            "queue_remaining": len(self.queue),
            "current_kind": current_kind,
            "current_code": current_code,
            "enqueue_count": self.request_count,
            "strength_request_count": self.strength_request_count,
            "orderbook_request_count": self.orderbook_request_count,
            "preopen_success_count": self.success_count,
            "strength_success_count": self.strength_success_count,
            "orderbook_success_count": self.orderbook_success_count,
            "partial_count": self.partial_count,
            "preopen_empty_count": self.empty_count,
            "preopen_error_count": self.error_count,
            "timeout_count": self.timeout_count,
            "retry_wait_count": retry_wait_count,
            "request_gap_sec": self.gap_sec,
            "hard_timeout_sec": self.timeout_sec,
            "pending_purge_count": self.pending_purge_count,
            "last_enqueued_code": self.last_code,
            "last_enqueued_kind": self.last_kind,
            "last_result_status": self.last_status,
            "last_cycle_at": self.last_tick_at,
            "last_error": self.last_error,
        }


# Backward-compatible alias used by the existing provider install wrapper.
OffhoursStrengthDrain = OffhoursMetricCompletionDrain


def install() -> None:
    """Replace the old strength-only off-hours drain with the unified controller."""

    from realtime_v2 import strength5m_definitive_preopen_patch as definitive

    if getattr(definitive, "_stockboard_offhours_metric_completion_installed", False):
        return
    definitive.OffhoursStrengthDrain = OffhoursMetricCompletionDrain
    definitive._stockboard_offhours_metric_completion_installed = True
