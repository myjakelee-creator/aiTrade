"""Program-net snapshot cache and background refresh for StockBoard.

Program net is not an event stream. Kiwoom ka90004 returns a day-level snapshot,
so the correct policy is latest-snapshot replacement, not accumulation.

This module patches the existing provider call before stockboard_server imports it:
- Save the latest ka90004 values to data/runtime/program_net.
- Restore the latest same-day snapshot when a fetch fails.
- Refresh in a low-priority background loop and update the in-memory mapping in
  place so running boards do not need a restart.
- Re-apply the latest snapshot to rows before candidate scoring/rendering.
"""

from __future__ import annotations

import json
import os
import sys
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock, Thread
from typing import Any

KST = timezone(timedelta(hours=9))
CACHE_SCHEMA_VERSION = 1
_DEFAULT_REFRESH_SEC = 60.0
_LOCK = RLock()
_LATEST_ENTRIES: dict[str, dict[str, Any]] = {}
_LATEST_QUERY_DATE = ""
_LATEST_PAYLOAD: dict[str, Any] | None = None
_BACKGROUND_THREADS: dict[str, Thread] = {}
_MUTABLE_VALUE_REFS: list[dict[str, Any]] = []


def _runtime_dir() -> Path:
    configured = os.getenv("STOCKBOARD_RUNTIME_DIR")
    return Path(configured) if configured else Path(__file__).resolve().parent / "data" / "runtime"


def _cache_path(query_date: Any) -> Path:
    date_text = "".join(ch for ch in str(query_date or "") if ch.isdigit())[:8]
    if len(date_text) != 8:
        date_text = datetime.now(KST).strftime("%Y%m%d")
    return _runtime_dir() / "program_net" / f"program_net_{date_text}.json"


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _age_sec(timestamp_text: Any) -> float | None:
    if not timestamp_text:
        return None
    try:
        dt = datetime.fromisoformat(str(timestamp_text).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return max(0.0, round((datetime.now(KST) - dt.astimezone(KST)).total_seconds(), 3))
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _stock_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("A") and len(text) == 7:
        text = text[1:]
    text = text.replace("_AL", "").replace("_NX", "")
    return text if len(text) == 6 and text.isdigit() else ""


def _refresh_interval_sec() -> float:
    text = os.getenv("STOCKBOARD_PROGRAM_NET_REFRESH_SEC", str(_DEFAULT_REFRESH_SEC))
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        value = _DEFAULT_REFRESH_SEC
    # Program net is a market-wide TR snapshot; keep it low-priority.
    return max(15.0, value)


def _entry_value(entry: Any) -> float | None:
    if isinstance(entry, dict):
        return _number(entry.get("program_net"))
    return _number(entry)


def _make_entries(values: dict[str, Any] | None, *, query_date: Any, status: str, source: str, updated_at: str, error: Any = None) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for raw_code, raw_value in (values or {}).items():
        code = _stock_code(raw_code)
        if not code:
            continue
        value = _entry_value(raw_value)
        if value is None:
            continue
        existing = raw_value if isinstance(raw_value, dict) else {}
        entry = {
            "stock_code": code,
            "program_net": value,
            "program_net_eok": value,
            "program_net_source": source,
            "program_net_status": status,
            "program_net_updated_at": updated_at,
            "program_net_query_date": str(query_date or ""),
            "program_net_stale_sec": _age_sec(updated_at),
        }
        for key in (
            "program_net_divisor",
            "program_net_market",
            "program_net_raw",
            "program_net_error",
        ):
            if key in existing:
                entry[key] = existing.get(key)
        if error is not None:
            entry["program_net_error"] = str(error)
        entries[code] = entry
    return entries


def _save_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    except OSError as error:
        print(f"warning: program net cache save failed: {error}", file=sys.stderr)


def _load_cache(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        entries = payload.get("entries")
        if not isinstance(entries, dict) or not entries:
            return None
        return payload
    except (OSError, json.JSONDecodeError) as error:
        print(f"warning: program net cache load failed: {error}", file=sys.stderr)
        return None


def _publish_entries(entries: dict[str, dict[str, Any]], query_date: Any, payload: dict[str, Any] | None = None) -> None:
    with _LOCK:
        _LATEST_ENTRIES.clear()
        _LATEST_ENTRIES.update(deepcopy(entries))
        global _LATEST_QUERY_DATE, _LATEST_PAYLOAD
        _LATEST_QUERY_DATE = str(query_date or "")
        _LATEST_PAYLOAD = deepcopy(payload) if isinstance(payload, dict) else None
        for ref in _MUTABLE_VALUE_REFS:
            ref.clear()
            ref.update(deepcopy(entries))


def _result_with_entries(result: dict[str, Any], entries: dict[str, dict[str, Any]], *, query_date: Any, cache_status: str, cache_path: Path, error: Any = None) -> dict[str, Any]:
    payload = dict(result or {})
    payload["values"] = entries
    payload["program_net_cache"] = {
        "status": cache_status,
        "query_date": str(query_date or ""),
        "cache_file": str(cache_path),
        "entry_count": len(entries),
        "error": str(error) if error is not None else None,
        "updated_at": _now_iso(),
        "refresh_interval_sec": _refresh_interval_sec(),
    }
    return payload


def _cache_result(query_date: Any, error: Any = None) -> dict[str, Any] | None:
    path = _cache_path(query_date)
    cached = _load_cache(path)
    if not cached:
        return None
    cached_entries = cached.get("entries") or {}
    updated_at = cached.get("updated_at") or cached.get("saved_at") or _now_iso()
    entries = _make_entries(
        cached_entries,
        query_date=query_date,
        status="cached",
        source="ka90004_cache",
        updated_at=updated_at,
        error=error,
    )
    if not entries:
        return None
    result = {
        "values": entries,
        "market_stats": cached.get("market_stats") or [],
        "market_counts": cached.get("market_counts") or {"KOSPI": 0, "KOSDAQ": 0},
        "raw_samples": cached.get("raw_samples") or [],
        "converted_samples": cached.get("converted_samples") or [],
        "divisor": cached.get("divisor"),
        "request_sleep_seconds": cached.get("request_sleep_seconds"),
        "errors": [{"market": None, "error": f"fetch failed; using cache: {error}"}] if error else [],
        "rate_limit": cached.get("rate_limit"),
    }
    wrapped = _result_with_entries(result, entries, query_date=query_date, cache_status="loaded", cache_path=path, error=error)
    _publish_entries(entries, query_date, wrapped)
    return wrapped


def _save_success_result(result: dict[str, Any], query_date: Any) -> dict[str, Any]:
    now_text = _now_iso()
    errors = result.get("errors") if isinstance(result.get("errors"), list) else []
    status = "partial" if errors or result.get("rate_limit") else "ok"
    entries = _make_entries(
        result.get("values") if isinstance(result, dict) else {},
        query_date=query_date,
        status=status,
        source="ka90004",
        updated_at=now_text,
    )
    path = _cache_path(query_date)
    wrapped = _result_with_entries(result, entries, query_date=query_date, cache_status="saved", cache_path=path)
    cache_payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "query_date": str(query_date or ""),
        "updated_at": now_text,
        "entries": entries,
        "market_stats": result.get("market_stats") or [],
        "market_counts": result.get("market_counts") or {"KOSPI": 0, "KOSDAQ": 0},
        "raw_samples": result.get("raw_samples") or [],
        "converted_samples": result.get("converted_samples") or [],
        "divisor": result.get("divisor"),
        "request_sleep_seconds": result.get("request_sleep_seconds"),
        "errors": errors,
        "rate_limit": result.get("rate_limit"),
    }
    if entries:
        _save_cache(path, cache_payload)
        _publish_entries(entries, query_date, wrapped)
    return wrapped


def apply_latest_program_net_to_rows(rows: Any) -> Any:
    if not isinstance(rows, list):
        return rows
    with _LOCK:
        entries = deepcopy(_LATEST_ENTRIES)
    if not entries:
        return rows
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = _stock_code(row.get("stock_code") or row.get("code"))
        entry = entries.get(code)
        if not isinstance(entry, dict):
            continue
        value = _entry_value(entry)
        if value is None:
            continue
        row["program_net"] = value
        row["program_net_eok"] = value
        row["program_net_source"] = entry.get("program_net_source")
        row["program_net_status"] = entry.get("program_net_status")
        row["program_net_updated_at"] = entry.get("program_net_updated_at")
        row["program_net_query_date"] = entry.get("program_net_query_date")
        row["program_net_stale_sec"] = _age_sec(entry.get("program_net_updated_at"))
        if entry.get("program_net_error"):
            row["program_net_error"] = entry.get("program_net_error")
    return rows


def _background_refresh_loop(original_fetch, access_token: str, query_date: Any, values_ref: dict[str, Any]) -> None:
    interval = _refresh_interval_sec()
    while True:
        time.sleep(interval)
        try:
            result = original_fetch(access_token, query_date)
            wrapped = _save_success_result(result, query_date)
            values_ref.clear()
            values_ref.update(deepcopy(wrapped.get("values") or {}))
        except Exception as error:
            cached = _cache_result(query_date, error)
            if cached:
                values_ref.clear()
                values_ref.update(deepcopy(cached.get("values") or {}))
            print(f"warning: program net background refresh failed: {error}", file=sys.stderr)


def _start_background_refresh(original_fetch, access_token: str, query_date: Any, values_ref: dict[str, Any]) -> None:
    key = str(query_date or "")
    with _LOCK:
        if values_ref not in _MUTABLE_VALUE_REFS:
            _MUTABLE_VALUE_REFS.append(values_ref)
        thread = _BACKGROUND_THREADS.get(key)
        if thread and thread.is_alive():
            return
        thread = Thread(
            target=_background_refresh_loop,
            args=(original_fetch, access_token, query_date, values_ref),
            name=f"stockboard-program-net-refresh-{key or 'today'}",
            daemon=True,
        )
        _BACKGROUND_THREADS[key] = thread
        thread.start()


def install_program_net_cache_patch() -> None:
    import kiwoom_data_provider
    import stockboard_engine

    if getattr(kiwoom_data_provider, "_program_net_cache_patch_installed", False):
        return

    original_fetch_program_net = kiwoom_data_provider.fetch_program_net
    original_prepare_display_rows = stockboard_engine.prepare_display_rows

    def fetch_program_net_with_cache(access_token, query_date):
        try:
            result = original_fetch_program_net(access_token, query_date)
        except Exception as error:
            cached = _cache_result(query_date, error)
            if cached:
                values_ref = cached.get("values") or {}
                _start_background_refresh(original_fetch_program_net, access_token, query_date, values_ref)
                return cached
            raise
        wrapped = _save_success_result(result, query_date)
        values_ref = wrapped.get("values") or {}
        _start_background_refresh(original_fetch_program_net, access_token, query_date, values_ref)
        return wrapped

    def prepare_display_rows_with_program_meta(top100_rows, tradable_codes, program_net_by_code):
        numeric_values = {}
        if isinstance(program_net_by_code, dict):
            for code, entry in program_net_by_code.items():
                value = _entry_value(entry)
                if value is not None:
                    numeric_values[code] = value
        display_rows = original_prepare_display_rows(top100_rows, tradable_codes, numeric_values)
        if isinstance(program_net_by_code, dict):
            for row in display_rows:
                code = _stock_code(row.get("stock_code"))
                entry = program_net_by_code.get(code)
                if isinstance(entry, dict):
                    value = _entry_value(entry)
                    if value is not None:
                        row["program_net"] = value
                        row["program_net_eok"] = value
                    row["program_net_source"] = entry.get("program_net_source")
                    row["program_net_status"] = entry.get("program_net_status")
                    row["program_net_updated_at"] = entry.get("program_net_updated_at")
                    row["program_net_query_date"] = entry.get("program_net_query_date")
                    row["program_net_stale_sec"] = _age_sec(entry.get("program_net_updated_at"))
                    if entry.get("program_net_error"):
                        row["program_net_error"] = entry.get("program_net_error")
        apply_latest_program_net_to_rows(display_rows)
        return display_rows

    kiwoom_data_provider.fetch_program_net = fetch_program_net_with_cache
    stockboard_engine.prepare_display_rows = prepare_display_rows_with_program_meta
    kiwoom_data_provider._program_net_cache_patch_installed = True
