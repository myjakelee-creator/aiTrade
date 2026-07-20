from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

PATCH_VERSION = "six_metric_lifecycle_runtime_opt_v2"


def install() -> None:
    """Reduce lifecycle overhead and make source precedence deterministic.

    The next-premarket boundary is calculated once per minute, captured_at uses a
    stable source timestamp, and cache loading follows daily → legacy session hold →
    unified lifecycle snapshot so the newest authoritative snapshot wins.
    """

    import realtime_v2.worker_six_metric_lifecycle_patch as module

    if getattr(module, "_stockboard_six_metric_runtime_opt_installed", False):
        return

    original_boundary = module._next_premarket_boundary
    original_entry = module._entry_from_values
    cache_lock = threading.RLock()
    boundary_cache: dict[str, Any] = {"key": None, "value": None}

    def cached_boundary(now: datetime | None = None):
        current = now or datetime.now()
        key = current.strftime("%Y%m%d%H%M")
        with cache_lock:
            if boundary_cache.get("key") == key and boundary_cache.get("value") is not None:
                return boundary_cache["value"]
        value = original_boundary(current)
        with cache_lock:
            boundary_cache["key"] = key
            boundary_cache["value"] = value
        return value

    def stable_entry(
        code: str,
        group: str,
        values: dict[str, Any],
        *,
        source_date: str,
        now: datetime,
    ):
        entry = original_entry(
            code,
            group,
            values,
            source_date=source_date,
            now=now,
        )
        if not isinstance(entry, dict):
            return entry
        captured_at = ""
        config = module.GROUPS.get(group) or {}
        for key in config.get("date_keys") or ():
            value = values.get(key)
            if value not in (None, ""):
                captured_at = str(value)
                break
        entry["captured_at"] = captured_at or str(entry.get("source_trading_date") or "")
        return entry

    def trusted_load_cache(now: datetime | None = None):
        current = now or datetime.now()
        session = module.market_session_now(current)
        expected = module._expected_date(session, current)
        cache = module._empty_cache()

        if expected:
            daily_path = module.RUNTIME_DIR / f"daily_state_{expected}.json"
            daily = module._read_json(daily_path)
            module._merge_payload(
                cache,
                daily,
                payload_date=module._date_digits(daily.get("trading_date")) or expected,
                now=current,
            )

        legacy_hold = module._read_json(module.SESSION_HOLD_PATH)
        module._merge_payload(
            cache,
            legacy_hold,
            payload_date=module._date_digits(legacy_hold.get("trading_date")),
            now=current,
        )

        unified = module._read_json(module.SNAPSHOT_PATH)
        module._merge_payload(
            cache,
            unified,
            payload_date=module._date_digits(unified.get("source_trading_date")),
            now=current,
        )

        for group in module.GROUPS:
            cache[group] = {
                code: entry
                for code, entry in cache[group].items()
                if module._entry_valid(
                    entry,
                    expected_date=expected,
                    group=group,
                    now=current,
                )
            }
        return cache

    module._next_premarket_boundary = cached_boundary
    module._entry_from_values = stable_entry
    module._load_cache = trusted_load_cache
    module._stockboard_six_metric_runtime_opt_installed = True
    module._stockboard_six_metric_runtime_opt_version = PATCH_VERSION
