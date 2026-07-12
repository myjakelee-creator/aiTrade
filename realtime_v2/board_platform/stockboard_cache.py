from __future__ import annotations

import json
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable


class StockBoardSnapshotCacheService(threading.Thread):
    """Compute the expensive StockBoard snapshot once and fan it out to all clients."""

    def __init__(
        self,
        state: Any,
        *,
        interval_sec: float = 0.10,
        opening_interval_sec: float = 0.25,
        slow_interval_sec: float = 0.50,
        degraded_interval_sec: float = 1.00,
        slow_threshold_ms: float = 60.0,
        degraded_threshold_ms: float = 100.0,
        heartbeat_sec: float = 2.0,
        encoder: Callable[[Any], str] | None = None,
    ) -> None:
        super().__init__(name="stockboard-v2-shared-snapshot-cache", daemon=True)
        self.state = state
        self.interval_sec = max(0.05, float(interval_sec))
        self.opening_interval_sec = max(self.interval_sec, float(opening_interval_sec))
        self.slow_interval_sec = max(self.opening_interval_sec, float(slow_interval_sec))
        self.degraded_interval_sec = max(self.slow_interval_sec, float(degraded_interval_sec))
        self.slow_threshold_ms = max(1.0, float(slow_threshold_ms))
        self.degraded_threshold_ms = max(self.slow_threshold_ms, float(degraded_threshold_ms))
        self.heartbeat_sec = max(0.5, float(heartbeat_sec))
        self.encoder = encoder or (
            lambda payload: json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self.cache_lock = threading.RLock()
        self.compute_lock = threading.Lock()
        self.clients = 0
        self.cache_version = 0
        self.payload: dict[str, Any] = {}
        self.payload_bytes = b""
        self.last_source_token: tuple[Any, ...] | None = None
        self.last_compute_mono = 0.0
        self.last_compute_ms: float | None = None
        self.last_serialize_ms: float | None = None
        self.last_payload_size = 0
        self.last_updated_at: str | None = None
        self.last_error: str | None = None
        self.compute_attempt_count = 0
        self.compute_success_count = 0
        self.compute_skip_count = 0
        self.compute_busy_skip_count = 0
        self.last_candidate_model: str | None = None
        self.degraded_mode = False
        self.slow_streak = 0
        self.recovery_streak = 0

    @staticmethod
    def _now_text() -> str:
        return datetime.now().isoformat(timespec="milliseconds")

    def _state_status(self) -> dict[str, Any] | None:
        lock = getattr(self.state, "lock", None)
        acquired = False
        if lock is not None:
            try:
                acquired = lock.acquire(blocking=False)
            except TypeError:
                acquired = lock.acquire(False)
            if not acquired:
                return None
        try:
            status = getattr(self.state, "status", {}) or {}
            return dict(status)
        finally:
            if lock is not None and acquired:
                lock.release()

    def _source_token(self) -> tuple[Any, ...] | None:
        status = self._state_status()
        if status is None:
            return None
        return (
            int(status.get("event_count") or 0),
            int(status.get("trade_count") or 0),
            int(status.get("orderbook_count") or 0),
            str(status.get("program_net_last_at") or ""),
            str(status.get("market_phase") or ""),
            int(status.get("display_order_version") or 0),
            str(getattr(self.state, "selected_candidate_model_id", "") or ""),
        )

    def _opening_window(self) -> bool:
        now = datetime.now()
        minute = now.hour * 60 + now.minute
        return 9 * 60 <= minute < 9 * 60 + 10

    def _next_delay(self) -> float:
        compute_ms = float(self.last_compute_ms or 0.0)
        if self.degraded_mode:
            return self.degraded_interval_sec
        if compute_ms >= self.slow_threshold_ms:
            return self.slow_interval_sec
        if self._opening_window():
            return self.opening_interval_sec
        return self.interval_sec

    def cache_age_ms(self) -> int | None:
        if self.last_compute_mono <= 0:
            return None
        return max(0, round((time.monotonic() - self.last_compute_mono) * 1000))

    def register_client(self) -> None:
        with self.cache_lock:
            self.clients += 1
        self.wake_event.set()

    def unregister_client(self) -> None:
        with self.cache_lock:
            self.clients = max(0, self.clients - 1)

    def request_refresh(self) -> None:
        self.wake_event.set()

    def set_candidate_model(self, model_id: str | None) -> None:
        model_id = str(model_id or "").strip()
        if model_id:
            self.state.selected_candidate_model_id = model_id
        self.request_refresh()

    def refresh(self, *, force: bool = False) -> bool:
        if not self.compute_lock.acquire(blocking=False):
            self.compute_busy_skip_count += 1
            return False
        try:
            self.compute_attempt_count += 1
            token = self._source_token()
            now_mono = time.monotonic()
            if token is None and not force:
                self.compute_skip_count += 1
                return False
            if (
                not force
                and self.payload_bytes
                and token == self.last_source_token
                and now_mono - self.last_compute_mono < self.heartbeat_sec
            ):
                self.compute_skip_count += 1
                return False

            compute_started = time.perf_counter()
            payload = self.state.snapshot(limit=300)
            compute_ms = (time.perf_counter() - compute_started) * 1000.0
            if not isinstance(payload, dict):
                raise TypeError("state.snapshot() did not return a mapping")

            next_version = self.cache_version + 1
            payload = dict(payload)
            payload_status = dict(payload.get("status") or {})
            payload_status.update(
                {
                    "stockboard_shared_cache": True,
                    "stockboard_cache_version": next_version,
                    "stockboard_cache_compute_ms": round(compute_ms, 3),
                }
            )
            payload["status"] = payload_status

            serialize_started = time.perf_counter()
            encoded = self.encoder(payload)
            body = encoded.encode("utf-8") if isinstance(encoded, str) else bytes(encoded)
            serialize_ms = (time.perf_counter() - serialize_started) * 1000.0

            updated_at = str(payload.get("ts") or self._now_text())
            if compute_ms >= self.degraded_threshold_ms:
                self.slow_streak += 1
                self.recovery_streak = 0
            elif compute_ms <= self.slow_threshold_ms:
                self.recovery_streak += 1
                self.slow_streak = 0
            else:
                self.slow_streak = 0
                self.recovery_streak = 0
            if self.slow_streak >= 3:
                self.degraded_mode = True
            elif self.degraded_mode and self.recovery_streak >= 5:
                self.degraded_mode = False

            with self.cache_lock:
                self.cache_version = next_version
                self.payload = payload
                self.payload_bytes = body
                self.last_source_token = token
                self.last_compute_mono = time.monotonic()
                self.last_compute_ms = round(compute_ms, 3)
                self.last_serialize_ms = round(serialize_ms, 3)
                self.last_payload_size = len(body)
                self.last_updated_at = updated_at
                self.last_error = None
                self.last_candidate_model = str(
                    getattr(self.state, "selected_candidate_model_id", "") or ""
                )
                self.compute_success_count += 1
            return True
        except Exception as error:
            self.last_error = f"{type(error).__name__}: {error}"
            return False
        finally:
            self.compute_lock.release()

    def get_payload(
        self,
        *,
        limit: int = 300,
        force_if_empty: bool = True,
        refresh_if_changed: bool = True,
    ) -> dict[str, Any]:
        if force_if_empty and not self.payload_bytes:
            self.refresh(force=True)
        elif refresh_if_changed:
            self.refresh(force=False)
        with self.cache_lock:
            payload = deepcopy(self.payload)
        rows = payload.get("rows")
        if isinstance(rows, list) and limit < len(rows):
            payload["rows"] = rows[: max(1, int(limit))]
            payload["row_count"] = len(payload["rows"])
        return payload

    def get_bytes(self) -> tuple[int, bytes]:
        if not self.payload_bytes:
            self.refresh(force=True)
        with self.cache_lock:
            return self.cache_version, self.payload_bytes

    def status(self) -> dict[str, Any]:
        with self.cache_lock:
            return {
                "ok": self.last_error is None,
                "state": "READY" if self.payload_bytes else "WAIT",
                "clients": self.clients,
                "cache_version": self.cache_version,
                "cache_age_ms": self.cache_age_ms(),
                "compute_ms": self.last_compute_ms,
                "serialize_ms": self.last_serialize_ms,
                "payload_bytes": self.last_payload_size,
                "last_updated_at": self.last_updated_at,
                "last_error": self.last_error,
                "candidate_model": self.last_candidate_model,
                "compute_attempt_count": self.compute_attempt_count,
                "compute_success_count": self.compute_success_count,
                "compute_skip_count": self.compute_skip_count,
                "compute_busy_skip_count": self.compute_busy_skip_count,
                "interval_ms": round(self._next_delay() * 1000),
                "opening_window": self._opening_window(),
                "degraded_mode": self.degraded_mode,
                "slow_streak": self.slow_streak,
            }

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            with self.cache_lock:
                clients = self.clients
            if clients <= 0:
                self.wake_event.wait(0.5)
                self.wake_event.clear()
                continue
            self.refresh(force=False)
            self.stop_event.wait(self._next_delay())
