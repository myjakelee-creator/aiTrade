from __future__ import annotations

import cProfile
import functools
import os
import pstats
import threading
import time
from typing import Any


_profile_lock = threading.RLock()
_rows_call_count = 0
_sample_count = 0
_last_payload: dict[str, Any] = {}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(str(os.getenv(name, default)).strip()))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _round_ms(seconds: Any) -> float:
    try:
        return round(float(seconds) * 1000.0, 3)
    except (TypeError, ValueError):
        return 0.0


def _entry_name(filename: str, line: int, function: str) -> str:
    if filename == "~":
        return function
    short = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return f"{short}:{line}:{function}"


def _build_payload(
    profile: cProfile.Profile,
    elapsed_ms: float,
    row_count: int | None,
    thread_name: str,
) -> dict[str, Any]:
    stats = pstats.Stats(profile)
    entries: list[dict[str, Any]] = []
    lock_candidate_ms = 0.0

    for (filename, line, function), values in stats.stats.items():
        primitive_calls, total_calls, total_time, cumulative_time, _callers = values
        name = _entry_name(str(filename), int(line), str(function))
        item = {
            "name": name,
            "primitive_calls": int(primitive_calls),
            "total_calls": int(total_calls),
            "self_ms": _round_ms(total_time),
            "cumulative_ms": _round_ms(cumulative_time),
        }
        entries.append(item)

        lower = name.lower()
        if any(marker in lower for marker in ("rlock", "lock.acquire", "<method 'acquire'", "__enter__")):
            lock_candidate_ms = max(lock_candidate_ms, item["cumulative_ms"])

    entries.sort(
        key=lambda item: (
            -float(item.get("cumulative_ms") or 0.0),
            -float(item.get("self_ms") or 0.0),
            str(item.get("name") or ""),
        )
    )
    top_limit = _env_int("STOCKBOARD_CPROFILE_TOP", 20, minimum=5)
    return {
        "hot_profile_enabled": True,
        "hot_profile_done": True,
        "hot_profile_sample_count": _sample_count,
        "hot_profile_sample_every": _env_int("STOCKBOARD_CPROFILE_EVERY", 20, minimum=1),
        "hot_profile_elapsed_ms": round(float(elapsed_ms), 3),
        "hot_profile_row_count": row_count,
        "hot_profile_lock_candidate_ms": round(lock_candidate_ms, 3),
        "hot_profile_thread": thread_name,
        "hot_profile_top": entries[:top_limit],
        "hot_profile_at": time.time(),
    }


def _publish(state: Any, payload: dict[str, Any]) -> None:
    status = getattr(state, "status", None)
    if not isinstance(status, dict):
        return
    lock = getattr(state, "lock", None)
    if lock is None:
        status.update(payload)
        return
    try:
        with lock:
            status.update(payload)
    except Exception:
        status.update(payload)


def install(base_module: Any) -> None:
    global _rows_call_count
    global _sample_count

    state_class = base_module.State
    if getattr(state_class, "_stockboard_hot_path_cprofile_installed", False):
        return

    sample_every = _env_int("STOCKBOARD_CPROFILE_EVERY", 20, minimum=1)
    max_samples = _env_int("STOCKBOARD_CPROFILE_MAX_SAMPLES", 1, minimum=1)
    target_thread = str(
        os.getenv("STOCKBOARD_CPROFILE_THREAD", "stockboard-v2-shared-snapshot-cache")
    ).strip()
    original_rows = state_class.rows

    @functools.wraps(original_rows)
    def profiled_rows(self, limit: int = 300):
        global _rows_call_count
        global _sample_count

        current_thread = threading.current_thread().name
        eligible_thread = target_thread in {"", "*"} or current_thread == target_thread
        if not eligible_thread:
            return original_rows(self, limit)

        with _profile_lock:
            _rows_call_count += 1
            should_sample = (
                _sample_count < max_samples
                and _rows_call_count % sample_every == 0
            )
        if not should_sample:
            return original_rows(self, limit)

        profiler = cProfile.Profile()
        started = time.perf_counter()
        rows = None
        profiler.enable()
        try:
            rows = original_rows(self, limit)
            return rows
        finally:
            profiler.disable()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with _profile_lock:
                _sample_count += 1
                payload = _build_payload(
                    profiler,
                    elapsed_ms,
                    len(rows) if isinstance(rows, list) else None,
                    current_thread,
                )
                payload["hot_profile_sample_count"] = _sample_count
                _last_payload.clear()
                _last_payload.update(payload)
            _publish(self, payload)

    state_class.rows = profiled_rows
    state_class._stockboard_hot_path_cprofile_installed = True

    from realtime_v2.board_platform.stockboard_cache import StockBoardSnapshotCacheService

    original_cache_status = StockBoardSnapshotCacheService.status

    @functools.wraps(original_cache_status)
    def profiled_cache_status(self):
        result = original_cache_status(self)
        state_status = getattr(self.state, "status", {}) or {}
        for key, value in state_status.items():
            if str(key).startswith("hot_profile_"):
                result[key] = value
        if "hot_profile_enabled" not in result:
            result.update(
                {
                    "hot_profile_enabled": True,
                    "hot_profile_done": False,
                    "hot_profile_sample_count": 0,
                    "hot_profile_sample_every": sample_every,
                    "hot_profile_target_thread": target_thread,
                    "hot_profile_top": [],
                }
            )
        return result

    StockBoardSnapshotCacheService.status = profiled_cache_status
