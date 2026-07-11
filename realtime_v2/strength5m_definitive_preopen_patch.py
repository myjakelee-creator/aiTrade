from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any

_PENDING_STATUSES = {"pending", "requested", "deferred"}
_TERMINAL_ERRORS = {"error", "timeout", "failed", "unavailable", "not_connected"}


def _timestamp_age_sec(value: Any) -> float | None:
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


def _provider_ready(provider, *, require_registered: bool = False) -> tuple[bool, str]:
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return False, "provider_lock_missing"
    with lock:
        check = getattr(provider, "_strength_probe_ready_state_locked", None)
        if not callable(check):
            return False, "provider_ready_check_missing"
        try:
            ready_result = check()
        except Exception as error:
            return False, f"provider_ready_check_failed:{error}"
        if isinstance(ready_result, tuple):
            ready = bool(ready_result[0])
            reason = str(ready_result[1] or ("ready" if ready else "not_ready"))
        else:
            ready = bool(ready_result)
            reason = "ready" if ready else "not_ready"
        pump_last_at = getattr(provider, "_qt_pump_last_at", None)
        pump_age = _timestamp_age_sec(pump_last_at)
        if ready and (pump_age is None or pump_age > 3.0):
            return False, "qt_pump_stale"
        if ready and require_registered:
            if not bool(getattr(provider, "_realreg_succeeded", False)):
                return False, "realreg_not_ready"
            if not getattr(provider, "_registered_codes", None):
                return False, "registered_codes_empty"
        return ready, reason


def _remove_strength_pending(provider, predicate) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    lock = getattr(provider, "_lock", None)
    if lock is None:
        return removed
    with lock:
        pending = getattr(provider, "_strength_probe_pending", None)
        if pending is None:
            return removed
        kept = deque()
        for raw_item in list(pending):
            item = raw_item if isinstance(raw_item, dict) else {"stock_code": ""}
            if predicate(item):
                removed.append(dict(item))
            else:
                kept.append(raw_item)
        pending.clear()
        pending.extend(kept)
        pending_codes = getattr(provider, "_strength_probe_pending_codes", None)
        if isinstance(pending_codes, set):
            pending_codes.clear()
            for item in kept:
                if isinstance(item, dict):
                    code = str(item.get("stock_code") or "")
                    if code:
                        pending_codes.add(code)
    return removed


def install() -> None:
    """Install one definitive preopen controller and delayed scheduler startup."""

    from realtime_v2 import strength5m_scheduler as scheduler_module

    if getattr(scheduler_module, "_stockboard_definitive_preopen_installed", False):
        return

    scheduler_class = scheduler_module.Strength5mScheduler
    base_idle = scheduler_class._idle
    base_stats = scheduler_class.stats

    provider_wait_poll_sec = max(
        0.1,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_PROVIDER_WAIT_POLL_SEC", "0.25")),
    )
    pending_watchdog_sec = max(
        10.0,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_PENDING_WATCHDOG_SEC", "20")),
    )
    cross_tr_gap_sec = max(
        1.05,
        float(os.getenv("STOCKBOARD_STRENGTH_5M_CROSS_TR_GAP_SEC", "1.25")),
    )

    def ensure_fields(self) -> None:
        defaults = {
            "preopen_block_reason": None,
            "preopen_provider_ready": False,
            "preopen_provider_ready_reason": "not_checked",
            "preopen_orphan_purge_count": 0,
            "preopen_orphan_purge_last_code": None,
            "preopen_watchdog_release_count": 0,
            "preopen_watchdog_last_code": None,
            "preopen_watchdog_last_kind": None,
            "preopen_strength_gap_remaining_sec": None,
            "preopen_cross_tr_gap_remaining_sec": None,
        }
        for name, value in defaults.items():
            if not hasattr(self, name):
                setattr(self, name, value)

    def mark_removed(self, item: dict[str, Any], kind: str, now_mono: float) -> None:
        ensure_fields(self)
        code = str(item.get("stock_code") or "")
        if kind == "orphan":
            self.preopen_orphan_purge_count += 1
            self.preopen_orphan_purge_last_code = code or None
            return
        self.preopen_watchdog_release_count += 1
        self.preopen_watchdog_last_code = code or None
        self.preopen_watchdog_last_kind = kind
        self.preopen_requested_at.pop(code, None)
        attempt = max(1, int(self.preopen_attempts.get(code, 1) or 1))
        self.preopen_retry_at[code] = now_mono + self._retry_delay(attempt)
        try:
            self.provider._strength_probe_error(
                code,
                f"preopen controller released stuck {kind}",
                requested_at=item.get("requested_at"),
                trading_date=item.get("trading_date"),
            )
        except Exception:
            pass

    def candidate_due(
        self,
        item: dict[str, Any],
        *,
        final_window: bool,
        now_mono: float | None = None,
    ) -> tuple[bool, float]:
        ensure_fields(self)
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("strength_status") or "").strip().lower()
        code = item["stock_code"]
        now_mono = time.monotonic() if now_mono is None else now_mono

        # Persisted requested/deferred markers from an earlier process are not live work.
        if status in _PENDING_STATUSES and code in self.preopen_requested_at:
            due_at = float(self.preopen_retry_at.get(code, 0.0) or 0.0)
            if now_mono < due_at:
                return False, due_at - now_mono

        if final_window and code not in self.preopen_final_attempted:
            return True, float("inf")

        due_at = float(self.preopen_retry_at.get(code, 0.0) or 0.0)
        if now_mono < due_at:
            return False, due_at - now_mono
        attempt = int(self.preopen_attempts.get(code, 0) or 0)
        return True, float("inf") if attempt == 0 else float(attempt)

    def idle(self, session=None, gap_override: float | None = None) -> bool:
        ensure_fields(self)
        if not self._preopen_window(session):
            self.preopen_block_reason = None
            return base_idle(self, session, gap_override=gap_override)

        provider = self.provider
        now_mono = time.monotonic()
        ready, reason = _provider_ready(provider)
        self.preopen_provider_ready = ready
        self.preopen_provider_ready_reason = reason

        if not ready:
            removed = _remove_strength_pending(
                provider,
                lambda item: str(item.get("stock_code") or "")
                not in self.preopen_requested_at,
            )
            for item in removed:
                mark_removed(self, item, "orphan", now_mono)
            self.preopen_block_reason = f"provider_not_ready:{reason}"
            return False

        lock = getattr(provider, "_lock", None)
        if lock is None:
            self.preopen_block_reason = "provider_lock_missing"
            return False

        release_item: dict[str, Any] | None = None
        release_kind: str | None = None
        with lock:
            strength_inflight = getattr(provider, "_strength_probe_inflight", None)
            if isinstance(strength_inflight, dict):
                started = strength_inflight.get("started_at_monotonic")
                try:
                    age = now_mono - float(started)
                except (TypeError, ValueError):
                    age = 0.0
                timeout = max(
                    1.0,
                    float(getattr(provider, "_strength_probe_timeout_sec", 10.0) or 10.0),
                )
                if age >= timeout + 3.0:
                    provider._strength_probe_inflight = None
                    release_item = dict(strength_inflight)
                    release_kind = "inflight"
                else:
                    self.preopen_block_reason = "strength_inflight"
                    return False

            pending = getattr(provider, "_strength_probe_pending", None)
            if release_item is None and pending:
                raw_item = pending[0]
                item = raw_item if isinstance(raw_item, dict) else {"stock_code": ""}
                code = str(item.get("stock_code") or "")
                requested_mono = self.preopen_requested_at.get(code)
                if requested_mono is None:
                    pending.popleft()
                    pending_codes = getattr(provider, "_strength_probe_pending_codes", None)
                    if hasattr(pending_codes, "discard"):
                        pending_codes.discard(code)
                    release_item = dict(item)
                    release_kind = "orphan"
                elif now_mono - float(requested_mono) >= pending_watchdog_sec:
                    pending.popleft()
                    pending_codes = getattr(provider, "_strength_probe_pending_codes", None)
                    if hasattr(pending_codes, "discard"):
                        pending_codes.discard(code)
                    release_item = dict(item)
                    release_kind = "pending"
                else:
                    self.preopen_block_reason = "strength_pending"
                    return False

            for block_reason, value in (
                ("orderbook_inflight", getattr(provider, "_orderbook_probe_inflight", None)),
                ("opt10055_inflight", getattr(provider, "_opt10055_probe_inflight", None)),
            ):
                if value:
                    self.preopen_block_reason = block_reason
                    return False
            for block_reason, value in (
                ("orderbook_pending", getattr(provider, "_orderbook_probe_pending", ())),
                ("opt10055_pending", getattr(provider, "_opt10055_probe_pending", ())),
            ):
                if len(value):
                    self.preopen_block_reason = block_reason
                    return False

            strength_last = float(
                getattr(provider, "_strength_probe_last_request_at", 0.0) or 0.0
            )
            cross_last = max(
                float(getattr(provider, "_orderbook_probe_last_request_at", 0.0) or 0.0),
                float(getattr(provider, "_opt10055_probe_last_request_at", 0.0) or 0.0),
            )

        if release_item is not None and release_kind is not None:
            mark_removed(self, release_item, release_kind, now_mono)
            self.preopen_block_reason = f"released_{release_kind}"
            return False

        strength_gap = (
            self.gap(session)
            if gap_override is None
            else max(self.normal_gap, float(gap_override))
        )
        strength_remaining = (
            0.0
            if strength_last <= 0
            else max(0.0, strength_gap - (now_mono - strength_last))
        )
        cross_remaining = (
            0.0
            if cross_last <= 0
            else max(0.0, cross_tr_gap_sec - (now_mono - cross_last))
        )
        self.preopen_strength_gap_remaining_sec = round(strength_remaining, 3)
        self.preopen_cross_tr_gap_remaining_sec = round(cross_remaining, 3)
        if strength_remaining > 0:
            self.preopen_block_reason = "strength_request_gap"
            return False
        if cross_remaining > 0:
            self.preopen_block_reason = "cross_tr_safety_gap"
            return False
        self.preopen_block_reason = "ready"
        return True

    def preopen_cycle(self, session, plan: list[dict[str, Any]]) -> None:
        ensure_fields(self)
        now = datetime.now()
        next_premarket = self._next_premarket(now)
        self.next_premarket_at = next_premarket.isoformat(timespec="seconds")
        self._observe_preopen_results(plan)

        missing = self._preopen_missing_items(plan)
        self.preopen_total_count = len(plan)
        self.preopen_missing_count = len(missing)
        self.preopen_filled_count = max(0, len(plan) - len(missing))
        if not missing:
            self.preopen_mode = "preopen_fill_complete"
            self.preopen_global_backoff_until = 0.0
            self.preopen_global_backoff_until_iso = None
            self.preopen_consecutive_errors = 0
            return

        now_mono = time.monotonic()
        if now_mono < self.preopen_global_backoff_until:
            self.preopen_mode = "preopen_fill_backoff"
            return

        ready, reason = _provider_ready(self.provider)
        self.preopen_provider_ready = ready
        self.preopen_provider_ready_reason = reason
        if not ready:
            _remove_strength_pending(self.provider, lambda _item: True)
            self.preopen_mode = "preopen_wait_provider"
            self.preopen_block_reason = f"provider_not_ready:{reason}"
            return

        final_window = self._prepare_final_pass(next_premarket, now)
        gap = self._preopen_gap(session, next_premarket, now)
        if not self._idle(session, gap_override=gap):
            self.preopen_mode = "preopen_fill_active"
            self.busy_skips += 1
            return

        lane_order = {"s1": 0, "top20": 1, "hidden50": 2, "top300": 3}
        candidates = []
        for index, item in enumerate(missing):
            due, score = self._preopen_candidate_due(
                item,
                final_window=final_window,
                now_mono=now_mono,
            )
            if due:
                candidates.append((lane_order[item["lane"]], -score, index, item))
        if not candidates:
            self.preopen_mode = "preopen_fill_active"
            self.preopen_block_reason = "retry_schedule"
            return

        _lane_priority, _score, _index, item = min(candidates)
        code, lane = item["stock_code"], item["lane"]

        ready, reason = _provider_ready(self.provider)
        if not ready:
            self.preopen_mode = "preopen_wait_provider"
            self.preopen_block_reason = f"provider_not_ready:{reason}"
            return

        source_trading_date = scheduler_module.last_completed_trading_date(now) or None
        response = self.provider.enqueue_strength_probe(
            code,
            priority="active" if lane in {"s1", "top20"} else "background",
            force=True,
            trading_date=source_trading_date,
        )
        status = str((response or {}).get("status") or "").strip().lower()
        if status in {"pending", "requested"}:
            self._mark_preopen_request(code, final_window=final_window)
            self.local_last[code] = time.monotonic()
            self.last_code, self.last_lane = code, lane
            self.enqueue_count += 1
            self.preopen_mode = "preopen_fill_active"
            self.preopen_block_reason = "strength_pending"
            return
        if status == "deferred":
            _remove_strength_pending(
                self.provider,
                lambda pending_item: str(pending_item.get("stock_code") or "") == code,
            )
            self.preopen_mode = "preopen_wait_provider"
            self.preopen_block_reason = "provider_became_not_ready"
            return
        if status in _TERMINAL_ERRORS:
            self.preopen_error_count += 1
            self.preopen_consecutive_errors += 1
            if self.preopen_consecutive_errors >= self.preopen_global_error_limit:
                self._enter_global_backoff()
                self.preopen_mode = "preopen_fill_backoff"
            else:
                self.preopen_mode = "preopen_fill_active"
            return
        self.preopen_empty_count += 1
        self.preopen_mode = "preopen_fill_active"

    def stats(self) -> dict[str, Any]:
        ensure_fields(self)
        result = base_stats(self)
        provider = self.provider
        ready, reason = _provider_ready(provider)
        lock = getattr(provider, "_lock", None)
        pending_count = 0
        pending_sample: list[str] = []
        inflight_code = None
        if lock is not None:
            with lock:
                pending_items = list(getattr(provider, "_strength_probe_pending", ()))
                pending_count = len(pending_items)
                pending_sample = [
                    str(item.get("stock_code") or "")
                    for item in pending_items[:20]
                    if isinstance(item, dict)
                ]
                inflight = getattr(provider, "_strength_probe_inflight", None)
                inflight_code = (
                    str(inflight.get("stock_code") or "")
                    if isinstance(inflight, dict)
                    else None
                )
        result.update(
            {
                "preopen_block_reason": self.preopen_block_reason,
                "preopen_provider_ready": ready,
                "preopen_provider_ready_reason": reason,
                "preopen_orphan_purge_count": self.preopen_orphan_purge_count,
                "preopen_orphan_purge_last_code": self.preopen_orphan_purge_last_code,
                "preopen_watchdog_release_count": self.preopen_watchdog_release_count,
                "preopen_watchdog_last_code": self.preopen_watchdog_last_code,
                "preopen_watchdog_last_kind": self.preopen_watchdog_last_kind,
                "preopen_strength_gap_remaining_sec": self.preopen_strength_gap_remaining_sec,
                "preopen_cross_tr_gap_remaining_sec": self.preopen_cross_tr_gap_remaining_sec,
                "provider_strength_pending_count": pending_count,
                "provider_strength_pending_sample": pending_sample,
                "provider_strength_inflight_code": inflight_code,
                "controller": "definitive_preopen_v1",
            }
        )
        return result

    scheduler_class._preopen_candidate_due = candidate_due
    scheduler_class._idle = idle
    scheduler_class._preopen_cycle = preopen_cycle
    scheduler_class.stats = stats

    def delayed_install(base) -> None:
        provider_class = base.KiwoomOpenApiRealtimeProvider
        if getattr(provider_class, "_stockboard_strength5m_delayed_installed", False):
            return

        original_register = provider_class.register_codes
        original_stop = provider_class.stop
        original_status = provider_class.status

        def enabled() -> bool:
            return str(os.getenv("STOCKBOARD_STRENGTH_5M_ENABLED", "1")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }

        def start_gate(provider) -> None:
            gate = getattr(provider, "_stockboard_strength5m_start_gate", None)
            if gate is not None and gate.is_alive():
                return
            stop_event = threading.Event()
            provider._stockboard_strength5m_gate_stop = stop_event

            def wait_and_start() -> None:
                while not stop_event.wait(provider_wait_poll_sec):
                    if not enabled():
                        return
                    scheduler = getattr(provider, "_stockboard_strength5m_scheduler", None)
                    if scheduler is not None and scheduler.is_alive():
                        return
                    ready, reason = _provider_ready(provider, require_registered=True)
                    provider._stockboard_strength5m_gate_reason = reason
                    if not ready:
                        continue
                    removed = _remove_strength_pending(provider, lambda _item: True)
                    provider._stockboard_strength5m_startup_purged = (
                        int(getattr(provider, "_stockboard_strength5m_startup_purged", 0) or 0)
                        + len(removed)
                    )
                    scheduler = scheduler_class(base, provider)
                    provider._stockboard_strength5m_scheduler = scheduler
                    scheduler.start()
                    provider._stockboard_strength5m_gate_reason = "started"
                    return

            gate = threading.Thread(
                target=wait_and_start,
                name="StockBoardStrength5mStartupGate",
                daemon=True,
            )
            provider._stockboard_strength5m_start_gate = gate
            gate.start()

        def register(provider, codes):
            result = original_register(provider, codes)
            if result and enabled():
                start_gate(provider)
            return result

        def stop(provider):
            gate_stop = getattr(provider, "_stockboard_strength5m_gate_stop", None)
            if gate_stop is not None:
                gate_stop.set()
            scheduler = getattr(provider, "_stockboard_strength5m_scheduler", None)
            if scheduler is not None:
                scheduler.stop()
                if scheduler.is_alive():
                    scheduler.join(timeout=2.0)
            gate = getattr(provider, "_stockboard_strength5m_start_gate", None)
            if gate is not None and gate.is_alive():
                gate.join(timeout=2.0)
            return original_stop(provider)

        def status(provider):
            result = original_status(provider)
            result = result if isinstance(result, dict) else {"status": result}
            scheduler = getattr(provider, "_stockboard_strength5m_scheduler", None)
            if scheduler is not None:
                scheduler_status = scheduler.stats()
            else:
                ready, reason = _provider_ready(provider, require_registered=True)
                gate = getattr(provider, "_stockboard_strength5m_start_gate", None)
                scheduler_status = {
                    "enabled": enabled(),
                    "alive": False,
                    "mode": "waiting_provider",
                    "provider_ready": ready,
                    "provider_ready_reason": reason,
                    "startup_gate_alive": bool(gate is not None and gate.is_alive()),
                    "startup_gate_reason": getattr(
                        provider,
                        "_stockboard_strength5m_gate_reason",
                        reason,
                    ),
                    "startup_pending_purge_count": int(
                        getattr(provider, "_stockboard_strength5m_startup_purged", 0) or 0
                    ),
                    "controller": "definitive_preopen_v1",
                }
            result["strength5m_scheduler"] = scheduler_status
            return result

        provider_class.register_codes = register
        provider_class.stop = stop
        provider_class.status = status
        provider_class._stockboard_strength5m_delayed_installed = True

    scheduler_module.install = delayed_install
    scheduler_module._stockboard_definitive_preopen_installed = True
