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
    """Cache expensive ranking snapshots while overlaying price/rate at full speed.

    The StockBoard SSE endpoint may ask for a snapshot every 100 ms.  Building one
    currently deep-copies the full universe, recalculates ranking/features/funnel,
    and applies display-order logic.  At the 09:00 burst that work competes with
    event ingestion.  This patch rebuilds the full internal snapshot at most every
    500 ms (or immediately for model/display-order changes), then serves each SSE
    request from the cached ranking with only the decision-critical live fields
    overlaid from the current quote store.
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
    heavy_interval_sec = heavy_interval_ms / 1000.0
    heavy_max_age_sec = heavy_max_age_ms / 1000.0

    original_init = state_class.__init__
    original_snapshot = state_class.snapshot

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

    def rebuild_locked(self) -> None:
        start = time.perf_counter()
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
        finished = time.monotonic()
        self._opening_burst_cache_meta = meta
        self._opening_burst_cache_rows = rows
        self._opening_burst_cache_built_mono = finished
        self._opening_burst_cache_built_at = now_text()
        self._opening_burst_cache_signature = signature(self)
        self._opening_burst_cache_build_count += 1
        self._opening_burst_cache_last_build_ms = round(
            (time.perf_counter() - start) * 1000.0, 3
        )
        self._opening_burst_cache_last_error = None

    def should_rebuild_locked(self, now_mono: float, current_signature) -> bool:
        if self._opening_burst_cache_meta is None:
            return True
        age = max(0.0, now_mono - self._opening_burst_cache_built_mono)
        previous = self._opening_burst_cache_signature
        if previous is None:
            return True
        # Model/universe/display-order changes must be reflected immediately.
        if current_signature[3:] != previous[3:]:
            return True
        if age >= heavy_max_age_sec:
            return True
        # Trade/orderbook/event changes trigger at most one heavy build per interval.
        return age >= heavy_interval_sec and current_signature[:3] != previous[:3]

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

        cache_hit = True
        current_signature = signature(self)
        now_mono = time.monotonic()
        with self._opening_burst_cache_lock:
            if should_rebuild_locked(self, now_mono, current_signature):
                cache_hit = False
                try:
                    rebuild_locked(self)
                except Exception as error:
                    self._opening_burst_cache_last_error = (
                        f"{type(error).__name__}: {error}"
                    )
                    if self._opening_burst_cache_meta is None:
                        raise
            else:
                self._opening_burst_cache_reuse_count += 1

            meta = deepcopy(self._opening_burst_cache_meta or {})
            cached_rows = self._opening_burst_cache_rows[:requested_limit]
            rows = [deepcopy(row) for row in cached_rows]
            built_mono = self._opening_burst_cache_built_mono
            built_at = self._opening_burst_cache_built_at
            source_signature = self._opening_burst_cache_signature
            build_count = self._opening_burst_cache_build_count
            reuse_count = self._opening_burst_cache_reuse_count
            last_build_ms = self._opening_burst_cache_last_build_ms
            last_error = self._opening_burst_cache_last_error
            internal_row_count = len(self._opening_burst_cache_rows)

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

        cache_age_ms = round(
            max(0.0, time.monotonic() - built_mono) * 1000.0, 3
        ) if built_mono else None
        metrics = {
            "opening_burst_cache_enabled": True,
            "opening_burst_heavy_interval_ms": heavy_interval_ms,
            "opening_burst_heavy_max_age_ms": heavy_max_age_ms,
            "opening_burst_status_write_interval_sec": status_write_interval_sec,
            "opening_burst_cache_hit": cache_hit,
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

    state_class.__init__ = patched_init
    state_class.snapshot = patched_snapshot
    base.write_status_loop = patched_write_status_loop
    state_class._stockboard_opening_burst_cache_installed = True
