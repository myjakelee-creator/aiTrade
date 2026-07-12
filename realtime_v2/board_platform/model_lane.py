from __future__ import annotations

import os
import threading
import time
from copy import deepcopy
from datetime import datetime
from typing import Any, Callable


_MODEL_FIELDS = {
    "entry_score",
    "confirmation_score",
    "focus_score",
    "score_top50",
    "score_top20",
    "score_top5",
    "grade_score",
    "display_grade_source",
    "entry_rank",
    "confirmation_rank",
    "focus_rank",
    "model_rank",
    "pool_rank",
    "funnel_rank",
    "pool_stage",
    "is_candidate",
    "desired_top20",
    "momentum",
    "model_validation_status",
    "required_feature_missing",
    "grade_guard_failures",
    "rank_rise_score",
    "amount_ratio_score",
    "instant_strength_score",
    "program_score",
    "large_trade_score",
    "combination_score",
}
_MODEL_PREFIXES = (
    "candidate_",
    "score_",
    "grade_",
)
_MODEL_EXCLUDED = {
    "_source_rank",
    "trade_value_rank",
    "rank",
    "rank_change",
    "amount_ratio",
    "amount_ratio_missing_reason",
}


def _code(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    text = str(row.get("stock_code") or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _strip_model_fields(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in list(result):
        if key in _MODEL_FIELDS or key.startswith(_MODEL_PREFIXES):
            result.pop(key, None)
    return result


def _model_overlay(source: dict[str, Any], enriched: dict[str, Any]) -> dict[str, Any]:
    overlay: dict[str, Any] = {}
    for key, value in enriched.items():
        if key in _MODEL_EXCLUDED:
            continue
        if (
            key in _MODEL_FIELDS
            or key.startswith(_MODEL_PREFIXES)
            or key not in source
        ):
            overlay[key] = deepcopy(value)
    return overlay


class StockBoardModelLaneService(threading.Thread):
    """Run the existing candidate model engine off the fast snapshot path.

    The fast/rank snapshot submits only the newest prepared rows and immediately
    reuses the last completed model result. The background lane keeps no backlog:
    repeated submissions replace the pending input and are coalesced into one
    model calculation at the configured interval.
    """

    def __init__(
        self,
        engine: Callable[..., list[dict[str, Any]]],
        *,
        interval_sec: float = 1.0,
        opening_interval_sec: float = 1.0,
    ) -> None:
        super().__init__(name="stockboard-v2-model-lane", daemon=True)
        self.engine = engine
        self.interval_sec = max(0.25, float(interval_sec))
        self.opening_interval_sec = max(0.25, float(opening_interval_sec))
        self.lock = threading.RLock()
        self.compute_lock = threading.Lock()
        self.wake_event = threading.Event()
        self.stop_event = threading.Event()

        self.pending_rows: list[dict[str, Any]] | None = None
        self.pending_request_model_id = ""
        self.submission_version = 0
        self.completed_submission_version = 0
        self.force_requested = False

        self.result_request_model_id = ""
        self.result_model_id = ""
        self.result_overlays: dict[str, dict[str, Any]] = {}
        self.result_order: dict[str, int] = {}
        self.last_compute_mono = 0.0
        self.last_compute_ms: float | None = None
        self.last_completed_at: str | None = None
        self.last_error: str | None = None
        self.in_progress = False

        self.submit_count = 0
        self.compute_count = 0
        self.compute_error_count = 0
        self.reuse_count = 0
        self.pending_return_count = 0
        self.discard_count = 0
        self.coalesced_submission_count = 0
        self.last_input_count = 0
        self.last_result_count = 0

    @staticmethod
    def _now_text() -> str:
        return datetime.now().isoformat(timespec="milliseconds")

    @staticmethod
    def _opening_window() -> bool:
        now = datetime.now()
        minute = now.hour * 60 + now.minute
        return 9 * 60 <= minute < 9 * 60 + 10

    def _active_interval(self) -> float:
        return self.opening_interval_sec if self._opening_window() else self.interval_sec

    def submit(self, rows: list[dict[str, Any]], model_id: str | None) -> None:
        request_model_id = str(model_id or "").strip()
        prepared = [dict(row) for row in rows if isinstance(row, dict)]
        with self.lock:
            self.pending_rows = prepared
            self.pending_request_model_id = request_model_id
            self.submission_version += 1
            self.submit_count += 1
            if request_model_id != self.result_request_model_id:
                self.force_requested = True
        self.wake_event.set()

    def request_model(self, model_id: str | None) -> None:
        request_model_id = str(model_id or "").strip()
        with self.lock:
            if request_model_id != self.pending_request_model_id:
                self.pending_request_model_id = request_model_id
                self.force_requested = True
                self.submission_version += 1
        self.wake_event.set()

    def apply(self, rows: list[dict[str, Any]], model_id: str | None) -> list[dict[str, Any]]:
        request_model_id = str(model_id or "").strip()
        with self.lock:
            usable = bool(self.result_overlays) and (
                request_model_id == self.result_request_model_id
            )
            overlays = self.result_overlays
            order = self.result_order
            result_model_id = self.result_model_id
            if usable:
                self.reuse_count += 1
            else:
                self.pending_return_count += 1

        merged_rows: list[dict[str, Any]] = []
        if not usable:
            for row in rows:
                current = _strip_model_fields(row)
                current.update(
                    {
                        "candidate_model_id": request_model_id or None,
                        "candidate_score": None,
                        "grade_score": None,
                        "candidate_status": "MODEL_PENDING",
                        "model_validation_status": "PENDING",
                        "is_candidate": False,
                        "desired_top20": False,
                    }
                )
                merged_rows.append(current)
            return merged_rows

        fallback_rank = len(order) + 1
        for index, row in enumerate(rows, start=1):
            current = _strip_model_fields(row)
            code = _code(current)
            overlay = overlays.get(code)
            if overlay:
                current.update(overlay)
            current["candidate_model_id"] = current.get("candidate_model_id") or result_model_id
            current["model_lane_reused"] = True
            current["_model_lane_sort"] = order.get(code, fallback_rank + index)
            merged_rows.append(current)

        merged_rows.sort(
            key=lambda row: (
                int(row.pop("_model_lane_sort", fallback_rank)),
                int(row.get("rank") or 10**9),
                _code(row),
            )
        )
        return merged_rows

    def enrich_or_reuse(
        self,
        rows: list[dict[str, Any]],
        model_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self.submit(rows, model_id)
        return self.apply(rows, model_id)

    def _remaining_delay_locked(self, now_mono: float | None = None) -> float:
        if self.force_requested or not self.result_overlays:
            return 0.0
        if self.submission_version <= self.completed_submission_version:
            return 0.5
        now_mono = time.monotonic() if now_mono is None else float(now_mono)
        return max(
            0.0,
            self.last_compute_mono + self._active_interval() - now_mono,
        )

    def compute_once(self, *, force: bool = False) -> bool:
        if not self.compute_lock.acquire(blocking=False):
            return False
        try:
            with self.lock:
                if not self.pending_rows:
                    return False
                if (
                    not force
                    and not self.force_requested
                    and self.submission_version <= self.completed_submission_version
                ):
                    return False
                remaining = self._remaining_delay_locked()
                if not force and remaining > 0:
                    return False

                source_rows = [dict(row) for row in self.pending_rows]
                request_model_id = self.pending_request_model_id
                source_version = self.submission_version
                previous_completed = self.completed_submission_version
                self.force_requested = False
                self.in_progress = True

            started = time.perf_counter()
            try:
                enriched = self.engine(source_rows, model_id=request_model_id or None)
                if not isinstance(enriched, list):
                    raise TypeError("candidate model engine returned a non-list result")
                enriched = [row for row in enriched if isinstance(row, dict)]
                source_by_code = {_code(row): row for row in source_rows if _code(row)}
                overlays: dict[str, dict[str, Any]] = {}
                order: dict[str, int] = {}
                for index, row in enumerate(enriched, start=1):
                    code = _code(row)
                    if not code:
                        continue
                    overlays[code] = _model_overlay(source_by_code.get(code, {}), row)
                    order[code] = index
                actual_model_id = str(
                    (enriched[0].get("candidate_model_id") if enriched else "")
                    or request_model_id
                    or ""
                )
                compute_ms = (time.perf_counter() - started) * 1000.0
            except Exception as error:
                with self.lock:
                    self.completed_submission_version = source_version
                    self.last_compute_mono = time.monotonic()
                    self.last_compute_ms = round(
                        (time.perf_counter() - started) * 1000.0,
                        3,
                    )
                    self.last_error = f"{type(error).__name__}: {error}"
                    self.compute_error_count += 1
                    self.in_progress = False
                return False

            with self.lock:
                if request_model_id != self.pending_request_model_id:
                    self.discard_count += 1
                    self.in_progress = False
                    self.wake_event.set()
                    return False

                self.result_request_model_id = request_model_id
                self.result_model_id = actual_model_id
                self.result_overlays = overlays
                self.result_order = order
                self.completed_submission_version = source_version
                self.last_compute_mono = time.monotonic()
                self.last_compute_ms = round(compute_ms, 3)
                self.last_completed_at = self._now_text()
                self.last_error = None
                self.compute_count += 1
                self.coalesced_submission_count += max(
                    0,
                    source_version - previous_completed - 1,
                )
                self.last_input_count = len(source_rows)
                self.last_result_count = len(overlays)
                self.in_progress = False
                has_newer = self.submission_version > source_version
            if has_newer:
                self.wake_event.set()
            return True
        finally:
            self.compute_lock.release()

    def age_ms(self) -> int | None:
        with self.lock:
            last_compute_mono = self.last_compute_mono
        if last_compute_mono <= 0:
            return None
        return max(0, round((time.monotonic() - last_compute_mono) * 1000))

    def status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": self.last_error is None,
                "state": (
                    "COMPUTING"
                    if self.in_progress
                    else "READY"
                    if self.result_overlays
                    else "PENDING"
                    if self.pending_rows
                    else "WAIT"
                ),
                "model_id": self.result_model_id or None,
                "request_model_id": self.result_request_model_id or None,
                "pending_model_id": self.pending_request_model_id or None,
                "compute_ms": self.last_compute_ms,
                "age_ms": self.age_ms(),
                "interval_ms": round(self._active_interval() * 1000),
                "last_completed_at": self.last_completed_at,
                "last_error": self.last_error,
                "submit_count": self.submit_count,
                "compute_count": self.compute_count,
                "compute_error_count": self.compute_error_count,
                "reuse_count": self.reuse_count,
                "pending_return_count": self.pending_return_count,
                "discard_count": self.discard_count,
                "coalesced_submission_count": self.coalesced_submission_count,
                "submission_version": self.submission_version,
                "completed_submission_version": self.completed_submission_version,
                "pending": self.submission_version > self.completed_submission_version,
                "last_input_count": self.last_input_count,
                "last_result_count": self.last_result_count,
                "background": True,
                "latest_only": True,
            }

    def stop(self) -> None:
        self.stop_event.set()
        self.wake_event.set()

    def run(self) -> None:
        while not self.stop_event.is_set():
            if self.compute_once(force=False):
                continue
            with self.lock:
                delay = self._remaining_delay_locked()
            self.wake_event.wait(max(0.01, min(0.5, delay)))
            self.wake_event.clear()


def install(large_module: Any, base_module: Any) -> StockBoardModelLaneService:
    if getattr(large_module, "_stockboard_model_lane_installed", False):
        return large_module._stockboard_model_lane_service

    original_engine = large_module.enrich_candidate_model_fields
    interval_sec = float(os.getenv("STOCKBOARD_MODEL_LANE_INTERVAL_SEC", "1.0"))
    opening_interval_sec = float(
        os.getenv("STOCKBOARD_MODEL_LANE_OPENING_INTERVAL_SEC", "1.0")
    )
    service = StockBoardModelLaneService(
        original_engine,
        interval_sec=interval_sec,
        opening_interval_sec=opening_interval_sec,
    )
    service.start()
    large_module.enrich_candidate_model_fields = service.enrich_or_reuse

    original_snapshot = base_module.State.snapshot

    def patched_snapshot(self, limit: int = 300):
        payload = original_snapshot(self, limit)
        if isinstance(payload, dict):
            status = dict(payload.get("status") or {})
            lane = service.status()
            status.update(
                {
                    "stockboard_model_lane": True,
                    "stockboard_model_lane_state": lane.get("state"),
                    "stockboard_model_lane_model_id": lane.get("model_id"),
                    "stockboard_model_lane_compute_ms": lane.get("compute_ms"),
                    "stockboard_model_lane_age_ms": lane.get("age_ms"),
                    "stockboard_model_lane_interval_ms": lane.get("interval_ms"),
                    "stockboard_model_lane_compute_count": lane.get("compute_count"),
                    "stockboard_model_lane_reuse_count": lane.get("reuse_count"),
                    "stockboard_model_lane_coalesced": lane.get(
                        "coalesced_submission_count"
                    ),
                    "stockboard_model_lane_pending": lane.get("pending"),
                    "stockboard_model_lane_last_error": lane.get("last_error"),
                }
            )
            payload["status"] = status
        return payload

    original_server_init = base_module.WebServer.__init__

    def patched_server_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        self.stockboard_model_lane = service
        state.stockboard_model_lane = service

    original_server_close = base_module.WebServer.server_close

    def patched_server_close(self):
        service.stop()
        if service.is_alive():
            service.join(timeout=2.0)
        return original_server_close(self)

    base_module.State.snapshot = patched_snapshot
    base_module.WebServer.__init__ = patched_server_init
    base_module.WebServer.server_close = patched_server_close
    base_module.stockboard_model_lane_service = service
    large_module._stockboard_model_lane_service = service
    large_module._stockboard_model_lane_installed = True
    return service
