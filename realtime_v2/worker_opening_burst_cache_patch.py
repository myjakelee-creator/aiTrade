from __future__ import annotations

import os
import threading
import time
from copy import deepcopy
from typing import Any

from realtime_v2.common import event_age_sec, normalize_code, now_text


_FAST_OVERLAY_FIELDS = (
    "price",
    "trade_price",
    "change_rate",
    "trade_time",
    "received_at",
    "fid20_lag_sec",
    "row_source",
    "source_code",
    "market_type_raw",
)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _display_version(state) -> int:
    controller = getattr(state, "display_order_controller", None)
    if controller is None:
        return 0
    try:
        status = controller.status()
        return int(status.get("version") or 0) if isinstance(status, dict) else 0
    except Exception:
        return 0


def install(base) -> None:
    """Move expensive candidate/ranking snapshots off the HTTP/SSE request path.

    A background thread owns full-universe deepcopy, feature scoring, funnel ranking,
    and display-order calculation. HTTP/SSE callers always receive the most recent
    completed heavy snapshot and only overlay the decision-critical live fields.
    Rebuild requests are coalesced to a single pending job, so the 09:00 burst cannot
    create a stale calculation backlog.
    """

    state_class = base.State
    if getattr(state_class, "_stockboard_opening_burst_cache_installed", False):
        return

    heavy_interval_ms = _env_int(
        "STOCKBOARD_HEAVY_SNAPSHOT_INTERVAL_MS", 500, 200, 5000
    )
    heavy_max_age_ms = _env_int(
        "STOCKBOARD_HEAVY_SNAPSHOT_MAX_AGE_MS", 2000, 500, 15000
    )
    status_write_interval_sec = _env_int(
        "STOCKBOARD_STATUS_WRITE_INTERVAL_SEC", 5, 1, 60
    )
    background_poll_ms = _env_int(
        "STOCKBOARD_BACKGROUND_REBUILD_POLL_MS", 50, 10, 500
    )
    heavy_interval_sec = heavy_interval_ms / 1000.0
    heavy_max_age_sec = heavy_max_age_ms / 1000.0
    background_poll_sec = background_poll_ms / 1000.0

    original_init = state_class.__init__
    original_snapshot = state_class.snapshot

    def signature(self) -> tuple[Any, ...]:
        with self.lock:
            status = self.status
            trade_count = int(status.get("trade_count") or 0)
            orderbook_count = int(status.get("orderbook_count") or 0)
            event_count = int(status.get("event_count") or 0)
            universe_count = int(status.get("universe_count") or len(self.quotes))
            model_id = str(getattr(self, "selected_candidate_model_id", "") or "")
        return (
            trade_count,
            orderbook_count,
            event_count,
            universe_count,
            model_id,
            _display_version(self),
        )

    def request_background_rebuild(
        self,
        *,
        reason: str,
        current_signature: tuple[Any, ...] | None = None,
        force: bool = False,
    ) -> None:
        current_signature = current_signature or signature(self)
        with self._opening_burst_cache_lock:
            if self._opening_burst_cache_build_inflight:
                if not self._opening_burst_cache_pending:
                    self._opening_burst_cache_request_count += 1
                else:
                    self._opening_burst_cache_coalesced_request_count += 1
                self._opening_burst_cache_pending = True
            elif self._opening_burst_cache_pending:
                self._opening_burst_cache_coalesced_request_count += 1
            else:
                self._opening_burst_cache_pending = True
                self._opening_burst_cache_request_count += 1
                self._opening_burst_cache_pending_since_mono = time.monotonic()

            self._opening_burst_cache_requested_signature = current_signature
            self._opening_burst_cache_requested_reason = reason
            self._opening_burst_cache_force_immediate = bool(
                self._opening_burst_cache_force_immediate or force
            )
            self._opening_burst_cache_last_request_at = now_text()
        self._opening_burst_cache_wakeup.set()

    def heavy_build(self) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        tuple[Any, ...],
        float,
    ]:
        start = time.perf_counter()
        source_signature = signature(self)
        with self.lock:
            universe_count = int(
                self.status.get("universe_count") or len(self.quotes) or 0
            )
        internal_limit = max(300, universe_count)
        payload = original_snapshot(self, internal_limit)
        rows = payload.get("rows") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise RuntimeError("heavy snapshot did not return rows")
        meta = dict(payload)
        meta.pop("rows", None)
        meta.pop("row_count", None)
        build_ms = round((time.perf_counter() - start) * 1000.0, 3)
        return meta, rows, source_signature, build_ms

    def background_loop(self) -> None:
        while not self._opening_burst_cache_stop_event.is_set():
            self._opening_burst_cache_wakeup.wait(timeout=background_poll_sec)
            self._opening_burst_cache_wakeup.clear()
            if self._opening_burst_cache_stop_event.is_set():
                break

            while not self._opening_burst_cache_stop_event.is_set():
                with self._opening_burst_cache_lock:
                    pending = bool(self._opening_burst_cache_pending)
                    force = bool(self._opening_burst_cache_force_immediate)
                    cache_ready = self._opening_burst_cache_meta is not None
                    built_mono = float(self._opening_burst_cache_built_mono or 0.0)
                if not pending:
                    break

                delay = 0.0
                if cache_ready and not force:
                    delay = max(
                        0.0,
                        heavy_interval_sec - (time.monotonic() - built_mono),
                    )
                if delay > 0:
                    if self._opening_burst_cache_wakeup.wait(timeout=delay):
                        self._opening_burst_cache_wakeup.clear()
                        continue
                    if self._opening_burst_cache_stop_event.is_set():
                        break

                with self._opening_burst_cache_lock:
                    if not self._opening_burst_cache_pending:
                        continue
                    build_token = int(self._opening_burst_cache_request_count)
                    build_reason = self._opening_burst_cache_requested_reason
                    self._opening_burst_cache_pending = False
                    self._opening_burst_cache_force_immediate = False
                    self._opening_burst_cache_build_inflight = True
                    self._opening_burst_cache_last_build_started_at = now_text()

                try:
                    meta, rows, source_signature, build_ms = heavy_build(self)
                    finished_mono = time.monotonic()
                    with self._opening_burst_cache_lock:
                        self._opening_burst_cache_meta = meta
                        self._opening_burst_cache_rows = rows
                        self._opening_burst_cache_built_mono = finished_mono
                        self._opening_burst_cache_built_at = now_text()
                        self._opening_burst_cache_signature = source_signature
                        self._opening_burst_cache_build_count += 1
                        self._opening_burst_cache_background_build_count += 1
                        self._opening_burst_cache_last_build_ms = build_ms
                        self._opening_burst_cache_last_build_reason = build_reason
                        self._opening_burst_cache_last_error = None
                        self._opening_burst_cache_last_completed_request_count = build_token
                        self._opening_burst_cache_pending_since_mono = None
                        self._opening_burst_cache_ready_event.set()
                except Exception as error:
                    with self._opening_burst_cache_lock:
                        self._opening_burst_cache_last_error = (
                            f"{type(error).__name__}: {error}"
                        )
                        self._opening_burst_cache_background_error_count += 1
                        if self._opening_burst_cache_meta is None:
                            self._opening_burst_cache_pending = True
                finally:
                    with self._opening_burst_cache_lock:
                        self._opening_burst_cache_build_inflight = False
                        pending_again = bool(self._opening_burst_cache_pending)

                if pending_again:
                    continue
                break

    def start_background_thread(self) -> None:
        with self._opening_burst_cache_lock:
            thread = self._opening_burst_cache_thread
            if thread is not None and thread.is_alive():
                return
            self._opening_burst_cache_thread = threading.Thread(
                target=background_loop,
                args=(self,),
                name="stockboard-candidate-background",
                daemon=True,
            )
            thread = self._opening_burst_cache_thread
        thread.start()

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._opening_burst_cache_lock = threading.RLock()
        self._opening_burst_cache_meta: dict[str, Any] | None = None
        self._opening_burst_cache_rows: list[dict[str, Any]] = []
        self._opening_burst_cache_built_mono = 0.0
        self._opening_burst_cache_built_at: str | None = None
        self._opening_burst_cache_signature: tuple[Any, ...] | None = None
        self._opening_burst_cache_build_count = 0
        self._opening_burst_cache_reuse_count = 0
        self._opening_burst_cache_last_build_ms: float | None = None
        self._opening_burst_cache_last_overlay_ms: float | None = None
        self._opening_burst_cache_last_error: str | None = None
        self._opening_burst_cache_pending = False
        self._opening_burst_cache_pending_since_mono: float | None = None
        self._opening_burst_cache_force_immediate = False
        self._opening_burst_cache_build_inflight = False
        self._opening_burst_cache_requested_signature: tuple[Any, ...] | None = None
        self._opening_burst_cache_requested_reason: str | None = None
        self._opening_burst_cache_last_request_at: str | None = None
        self._opening_burst_cache_last_build_started_at: str | None = None
        self._opening_burst_cache_last_build_reason: str | None = None
        self._opening_burst_cache_request_count = 0
        self._opening_burst_cache_coalesced_request_count = 0
        self._opening_burst_cache_last_completed_request_count = 0
        self._opening_burst_cache_background_build_count = 0
        self._opening_burst_cache_background_error_count = 0
        self._opening_burst_cache_last_overlay_ms = None
        self._opening_burst_cache_wakeup = threading.Event()
        self._opening_burst_cache_stop_event = threading.Event()
        self._opening_burst_cache_ready_event = threading.Event()
        self._opening_burst_cache_thread: threading.Thread | None = None
        start_background_thread(self)
        request_background_rebuild(
            self,
            reason="startup",
            current_signature=signature(self),
            force=True,
        )

    def schedule_reason(self, current_signature, now_mono: float):
        with self._opening_burst_cache_lock:
            meta_ready = self._opening_burst_cache_meta is not None
            previous = self._opening_burst_cache_signature
            age = max(
                0.0,
                now_mono - float(self._opening_burst_cache_built_mono or 0.0),
            )
        if not meta_ready or previous is None:
            return "cold_start", True
        if current_signature[3:] != previous[3:]:
            return "structure_change", True
        if age >= heavy_max_age_sec:
            return "max_age", True
        if age >= heavy_interval_sec and current_signature[:3] != previous[:3]:
            return "event_change", False
        return None, False

    def bootstrap_payload(self, requested_limit: int) -> dict[str, Any]:
        with self.lock:
            rows = [deepcopy(row) for row in self.quotes.values()]
            current_status = deepcopy(self.status)
        try:
            current_status.update(self.logger_stats())
        except Exception:
            pass
        rows.sort(
            key=lambda row: (
                -(float(row.get("trade_value_eok") or 0.0)),
                int(row.get("seed_rank") or 999999),
                str(row.get("stock_code") or ""),
            )
        )
        rows = rows[:requested_limit]
        for index, row in enumerate(rows, start=1):
            row.setdefault("rank", index)
        return {
            "schema_version": 1,
            "source": "stockboard_v2_background_warmup",
            "ts": now_text(),
            "status": current_status,
            "row_count": len(rows),
            "rows": rows,
        }

    def live_overlays(self, codes: list[str]):
        overlays: dict[str, dict[str, Any]] = {}
        with self.lock:
            current_status = deepcopy(self.status)
            for code in codes:
                quote = self.quotes.get(code)
                if not isinstance(quote, dict):
                    continue
                overlays[code] = {
                    key: deepcopy(quote.get(key))
                    for key in _FAST_OVERLAY_FIELDS
                    if key in quote
                }
        try:
            current_status.update(self.logger_stats())
        except Exception:
            pass
        return overlays, current_status

    def patched_snapshot(self, limit: int = 300) -> dict[str, Any]:
        try:
            requested_limit = max(1, int(limit))
        except (TypeError, ValueError):
            requested_limit = 300

        start_background_thread(self)
        current_signature = signature(self)
        now_mono = time.monotonic()
        reason, force = schedule_reason(self, current_signature, now_mono)
        if reason:
            request_background_rebuild(
                self,
                reason=reason,
                current_signature=current_signature,
                force=force,
            )

        with self._opening_burst_cache_lock:
            cache_ready = self._opening_burst_cache_meta is not None
            if cache_ready:
                meta = deepcopy(self._opening_burst_cache_meta or {})
                cached_rows = self._opening_burst_cache_rows[:requested_limit]
                rows = [deepcopy(row) for row in cached_rows]
                built_mono = self._opening_burst_cache_built_mono
                built_at = self._opening_burst_cache_built_at
                source_signature = self._opening_burst_cache_signature
                self._opening_burst_cache_reuse_count += 1
            else:
                meta = {}
                rows = []
                built_mono = 0.0
                built_at = None
                source_signature = None
            build_count = self._opening_burst_cache_build_count
            reuse_count = self._opening_burst_cache_reuse_count
            last_build_ms = self._opening_burst_cache_last_build_ms
            last_error = self._opening_burst_cache_last_error
            internal_row_count = len(self._opening_burst_cache_rows)
            pending = self._opening_burst_cache_pending
            build_inflight = self._opening_burst_cache_build_inflight
            pending_reason = self._opening_burst_cache_requested_reason
            request_count = self._opening_burst_cache_request_count
            coalesced_count = self._opening_burst_cache_coalesced_request_count
            background_build_count = self._opening_burst_cache_background_build_count
            background_error_count = self._opening_burst_cache_background_error_count
            thread = self._opening_burst_cache_thread

        if not cache_ready:
            warmup = bootstrap_payload(self, requested_limit)
            meta = dict(warmup)
            rows = list(meta.pop("rows", []))
            meta.pop("row_count", None)

        overlay_start = time.perf_counter()
        codes = [normalize_code(row.get("stock_code")) for row in rows]
        overlays, current_status = live_overlays(
            self, [code for code in codes if code]
        )
        for row in rows:
            code = normalize_code(row.get("stock_code"))
            overlay = overlays.get(code)
            if not overlay:
                continue
            row.update(overlay)
            received_at = row.get("received_at")
            if received_at:
                row["price_age_sec"] = event_age_sec(received_at)

        overlay_ms = round((time.perf_counter() - overlay_start) * 1000.0, 3)
        with self._opening_burst_cache_lock:
            self._opening_burst_cache_last_overlay_ms = overlay_ms

        cache_age_ms = (
            round(max(0.0, time.monotonic() - built_mono) * 1000.0, 3)
            if built_mono
            else None
        )
        metrics = {
            "opening_burst_cache_enabled": True,
            "opening_burst_background_enabled": True,
            "opening_burst_background_thread_alive": bool(
                thread is not None and thread.is_alive()
            ),
            "opening_burst_background_ready": cache_ready,
            "opening_burst_background_pending": bool(pending),
            "opening_burst_background_build_inflight": bool(build_inflight),
            "opening_burst_background_pending_reason": pending_reason,
            "opening_burst_background_request_count": request_count,
            "opening_burst_background_coalesced_request_count": coalesced_count,
            "opening_burst_background_build_count": background_build_count,
            "opening_burst_background_error_count": background_error_count,
            "opening_burst_heavy_interval_ms": heavy_interval_ms,
            "opening_burst_heavy_max_age_ms": heavy_max_age_ms,
            "opening_burst_background_poll_ms": background_poll_ms,
            "opening_burst_status_write_interval_sec": status_write_interval_sec,
            "opening_burst_cache_hit": cache_ready,
            "opening_burst_cache_build_count": build_count,
            "opening_burst_cache_reuse_count": reuse_count,
            "opening_burst_cache_last_build_ms": last_build_ms,
            "opening_burst_cache_last_overlay_ms": overlay_ms,
            "opening_burst_cache_age_ms": cache_age_ms,
            "opening_burst_cache_built_at": built_at,
            "opening_burst_cache_source_event_count": (
                source_signature[2] if source_signature else None
            ),
            "opening_burst_internal_row_count": internal_row_count,
            "opening_burst_requested_row_count": requested_limit,
            "opening_burst_cache_last_error": last_error,
        }
        current_status.update(metrics)
        with self.lock:
            self.status.update(metrics)

        meta["ts"] = now_text()
        meta["status"] = current_status
        meta["rows"] = rows
        meta["row_count"] = len(rows)
        return meta

    def patched_write_status_loop(state, output_path, stop_event) -> None:
        with state.lock:
            state.status["opening_burst_status_write_interval_sec"] = (
                status_write_interval_sec
            )
        while not stop_event.wait(float(status_write_interval_sec)):
            try:
                base.atomic_write_json(output_path, state.snapshot(limit=300))
                state.persist_daily_state_if_needed()
            except Exception as error:
                with state.lock:
                    state.status["status_write_last_error"] = (
                        f"{type(error).__name__}: {error}"
                    )
        try:
            state._opening_burst_cache_stop_event.set()
            state._opening_burst_cache_wakeup.set()
        except Exception:
            pass

    state_class.__init__ = patched_init
    state_class.snapshot = patched_snapshot
    base.write_status_loop = patched_write_status_loop
    state_class._stockboard_opening_burst_cache_installed = True
