from __future__ import annotations

import threading
import time
from typing import Any, Callable

from realtime_v2.board_data_hub import BoardDataHub, StaleProjectionInput
from realtime_v2.common import now_text


ProjectionBuilder = Callable[
    [int, tuple[dict[str, Any], ...], dict[str, Any]],
    dict[str, Any],
]


class LatestOnlyProjectionWorker:
    """Build one projection from the latest shared feature snapshot.

    The queue depth is one. New feature versions replace an older pending version,
    and an obsolete result is discarded instead of being published. The worker
    never performs TR calls and never runs on an HTTP/SSE request thread.
    """

    def __init__(
        self,
        *,
        name: str,
        hub: BoardDataHub,
        builder: ProjectionBuilder,
        min_interval_ms: int = 500,
    ) -> None:
        self.name = str(name or "").strip().lower()
        if not self.name:
            raise ValueError("projection worker name is required")
        self.hub = hub
        self.builder = builder
        self.min_interval_sec = max(0.05, float(min_interval_ms) / 1000.0)
        self._lock = threading.RLock()
        self._wakeup = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_feature_version = 0
        self._completed_feature_version = 0
        self._request_count = 0
        self._coalesced_request_count = 0
        self._build_count = 0
        self._publish_count = 0
        self._stale_discard_count = 0
        self._error_count = 0
        self._build_inflight = False
        self._last_build_ms: float | None = None
        self._last_request_at: str | None = None
        self._last_started_at: str | None = None
        self._last_published_at: str | None = None
        self._last_error: str | None = None
        self._last_build_mono = 0.0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"board-projection-{self.name}",
                daemon=True,
            )
            thread = self._thread
        thread.start()
        self._publish_status()

    def stop(self) -> None:
        self._stop.set()
        self._wakeup.set()

    def submit(self, feature_version: int) -> None:
        version = max(0, int(feature_version or 0))
        if version <= 0:
            return
        with self._lock:
            if version <= self._completed_feature_version and not self._build_inflight:
                return
            if self._pending_feature_version:
                self._coalesced_request_count += 1
            else:
                self._request_count += 1
            self._pending_feature_version = max(self._pending_feature_version, version)
            self._last_request_at = now_text()
        self._wakeup.set()
        self._publish_status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            thread = self._thread
            return {
                "enabled": True,
                "name": self.name,
                "thread_alive": bool(thread is not None and thread.is_alive()),
                "pending_feature_version": self._pending_feature_version,
                "completed_feature_version": self._completed_feature_version,
                "build_inflight": self._build_inflight,
                "request_count": self._request_count,
                "coalesced_request_count": self._coalesced_request_count,
                "build_count": self._build_count,
                "publish_count": self._publish_count,
                "stale_discard_count": self._stale_discard_count,
                "error_count": self._error_count,
                "last_build_ms": self._last_build_ms,
                "last_request_at": self._last_request_at,
                "last_started_at": self._last_started_at,
                "last_published_at": self._last_published_at,
                "last_error": self._last_error,
                "queue_policy": "latest_only_depth_1",
                "input_policy": "shared_feature_snapshot_only",
                "direct_tr_allowed": False,
            }

    def _publish_status(self) -> None:
        self.hub.update_projection_status(self.name, self.status())

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wakeup.wait(timeout=0.5)
            self._wakeup.clear()
            if self._stop.is_set():
                break

            while not self._stop.is_set():
                with self._lock:
                    target_version = self._pending_feature_version
                    self._pending_feature_version = 0
                if target_version <= 0:
                    break

                delay = max(
                    0.0,
                    self.min_interval_sec - (time.monotonic() - self._last_build_mono),
                )
                if delay > 0 and self._stop.wait(delay):
                    break

                feature_version, _state_version, rows, meta = self.hub.borrow_feature_snapshot()
                if feature_version <= 0:
                    with self._lock:
                        self._pending_feature_version = max(
                            self._pending_feature_version, target_version
                        )
                    break

                started = time.perf_counter()
                with self._lock:
                    self._build_inflight = True
                    self._last_started_at = now_text()
                self._publish_status()

                try:
                    payload = self.builder(feature_version, rows, meta)
                    if not isinstance(payload, dict):
                        raise TypeError("projection builder must return dict")
                    with self._lock:
                        self._build_count += 1
                        self._last_build_ms = round(
                            (time.perf_counter() - started) * 1000.0, 3
                        )
                        self._last_build_mono = time.monotonic()
                    self.hub.publish_projection(
                        self.name,
                        payload,
                        input_feature_version=feature_version,
                    )
                    with self._lock:
                        self._publish_count += 1
                        self._completed_feature_version = feature_version
                        self._last_published_at = now_text()
                        self._last_error = None
                except StaleProjectionInput:
                    with self._lock:
                        self._stale_discard_count += 1
                        current_version = self.hub.borrow_feature_snapshot()[0]
                        self._pending_feature_version = max(
                            self._pending_feature_version, current_version
                        )
                except Exception as error:
                    with self._lock:
                        self._error_count += 1
                        self._last_error = f"{type(error).__name__}: {error}"
                finally:
                    with self._lock:
                        self._build_inflight = False
                        pending_again = self._pending_feature_version > 0
                    self._publish_status()

                if not pending_again:
                    break
