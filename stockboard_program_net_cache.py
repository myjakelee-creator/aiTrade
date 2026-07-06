"""Safe program-net cache wrapper for StockBoard.

Program net is a day-level snapshot, not an event stream. This wrapper only
adds a small startup-safe cache/fallback layer around the existing ka90004
fetch. It deliberately does not start background threads and does not monkey
patch row rendering logic.
"""

from __future__ import annotations

import json
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
CACHE_SCHEMA_VERSION = 1


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


def _clean_values(values: dict[str, Any] | None) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for raw_code, raw_value in (values or {}).items():
        code = _stock_code(raw_code)
        value = _number(raw_value.get("program_net") if isinstance(raw_value, dict) else raw_value)
        if code and value is not None:
            cleaned[code] = value
    return cleaned


def _load_cache(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        values = _clean_values(payload.get("values"))
        if not values:
            return None
        payload["values"] = values
        return payload
    except (OSError, json.JSONDecodeError) as error:
        print(f"warning: program net cache load failed: {error}", file=sys.stderr)
        return None


def _save_cache(path: Path, result: dict[str, Any], query_date: Any) -> None:
    values = _clean_values(result.get("values") if isinstance(result, dict) else {})
    if not values:
        return
    payload = {
        "schema_version": CACHE_SCHEMA_VERSION,
        "query_date": str(query_date or ""),
        "updated_at": _now_iso(),
        "values": values,
        "market_stats": deepcopy(result.get("market_stats") or []),
        "market_counts": deepcopy(result.get("market_counts") or {"KOSPI": 0, "KOSDAQ": 0}),
        "raw_samples": deepcopy(result.get("raw_samples") or []),
        "converted_samples": deepcopy(result.get("converted_samples") or []),
        "divisor": result.get("divisor"),
        "request_sleep_seconds": result.get("request_sleep_seconds"),
        "errors": deepcopy(result.get("errors") or []),
        "rate_limit": result.get("rate_limit"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        tmp_path.replace(path)
    except OSError as error:
        print(f"warning: program net cache save failed: {error}", file=sys.stderr)


def _cached_result(path: Path, query_date: Any, error: Any = None) -> dict[str, Any] | None:
    cached = _load_cache(path)
    if not cached:
        return None
    errors = deepcopy(cached.get("errors") or [])
    if error is not None:
        errors.append({"market": None, "error": f"using program net cache after fetch issue: {error}"})
    return {
        "values": cached["values"],
        "market_stats": deepcopy(cached.get("market_stats") or []),
        "market_counts": deepcopy(cached.get("market_counts") or {"KOSPI": 0, "KOSDAQ": 0}),
        "raw_samples": deepcopy(cached.get("raw_samples") or []),
        "converted_samples": deepcopy(cached.get("converted_samples") or []),
        "divisor": cached.get("divisor"),
        "request_sleep_seconds": cached.get("request_sleep_seconds"),
        "errors": errors,
        "rate_limit": cached.get("rate_limit"),
        "program_net_cache": {
            "status": "loaded",
            "cache_file": str(path),
            "query_date": str(query_date or ""),
            "updated_at": cached.get("updated_at"),
            "entry_count": len(cached["values"]),
            "error": str(error) if error is not None else None,
        },
    }


def _attach_cache_meta(result: dict[str, Any], path: Path, query_date: Any, status: str) -> dict[str, Any]:
    result.setdefault("program_net_cache", {})
    result["program_net_cache"].update(
        {
            "status": status,
            "cache_file": str(path),
            "query_date": str(query_date or ""),
            "updated_at": _now_iso(),
            "entry_count": len(_clean_values(result.get("values"))),
        }
    )
    return result


def install_program_net_cache_patch() -> None:
    import kiwoom_data_provider

    if getattr(kiwoom_data_provider, "_program_net_cache_patch_installed", False):
        return

    original_fetch_program_net = kiwoom_data_provider.fetch_program_net

    def fetch_program_net_with_cache(access_token, query_date):
        path = _cache_path(query_date)
        try:
            result = original_fetch_program_net(access_token, query_date)
        except Exception as error:
            cached = _cached_result(path, query_date, error)
            if cached:
                return cached
            raise
        values = _clean_values(result.get("values") if isinstance(result, dict) else {})
        if values:
            result["values"] = values
            _save_cache(path, result, query_date)
            return _attach_cache_meta(result, path, query_date, "saved")
        cached = _cached_result(path, query_date, "empty ka90004 result")
        if cached:
            return cached
        return _attach_cache_meta(result, path, query_date, "empty_no_cache")

    kiwoom_data_provider.fetch_program_net = fetch_program_net_with_cache
    kiwoom_data_provider._program_net_cache_patch_installed = True
