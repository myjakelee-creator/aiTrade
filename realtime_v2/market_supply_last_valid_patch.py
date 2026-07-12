from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path
from typing import Any


MARKET_ALIASES = {
    "kospi": ("kospi", "KOSPI", "Kospi", "ks", "KS", "0", "001"),
    "kosdaq": ("kosdaq", "KOSDAQ", "Kosdaq", "kq", "KQ", "1", "101"),
}

FIELD_ALIASES = {
    "market_name": ("market_name", "market", "name", "label", "시장"),
    "market_index": (
        "market_index",
        "index",
        "index_value",
        "지수",
        "cur_prc",
        "current_index",
    ),
    "market_change_rate": (
        "market_change_rate",
        "change_rate",
        "change_percent",
        "등락률",
        "flu_rt",
        "rate",
    ),
    "advancers": (
        "advancers",
        "advance",
        "advance_count",
        "상승",
        "rising",
        "up_count",
    ),
    "upper_limit_count": (
        "upper_limit_count",
        "upper_limit",
        "상한",
        "upl",
    ),
    "decliners": (
        "decliners",
        "decline",
        "decline_count",
        "하락",
        "fall",
        "down_count",
    ),
    "lower_limit_count": (
        "lower_limit_count",
        "lower_limit",
        "하한",
        "lst",
    ),
    "individual_eok": (
        "individual_eok",
        "individual",
        "개인",
        "ind_netprps",
    ),
    "foreign_futures_eok": (
        "foreign_futures_eok",
        "foreign_futures",
        "외선",
    ),
    "foreign_spot_eok": (
        "foreign_spot_eok",
        "foreign",
        "외인",
        "frgnr_netprps",
    ),
    "institution_eok": (
        "institution_eok",
        "institution",
        "기관",
        "orgn_netprps",
    ),
    "program_market_eok": (
        "program_market_eok",
        "program",
        "프로",
        "all_netprps",
    ),
}

META_KEYS = (
    "schema_version",
    "source",
    "source_file",
    "ts",
    "copied_at",
    "query_date",
    "flow_date",
    "errors",
    "_status",
)

_WRITE_LOCK = threading.RLock()


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _first(mapping: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _market_from_rows(rows: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(rows, list):
        return result

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(
            _first(
                row,
                (
                    "market_name",
                    "market",
                    "name",
                    "label",
                    "시장",
                    "mrkt_tp",
                    "market_type",
                    "inds_cd",
                ),
            )
            or ""
        ).upper()
        if "KOSDAQ" in name or name in {
            "1",
            "101",
            "KQ",
            "P10102",
            "P101_AL02",
        }:
            result["kosdaq"] = row
        elif "KOSPI" in name or name in {
            "0",
            "001",
            "KS",
            "P00101",
            "P001_AL01",
        }:
            result["kospi"] = row
    return result


def _unwrap_market_supply(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    queue: list[dict[str, Any]] = [payload]
    seen: set[int] = set()
    wrapper_keys = (
        "market_supply",
        "values",
        "result",
        "payload",
        "data",
        "output",
    )

    while queue:
        candidate = queue.pop(0)
        candidate_id = id(candidate)
        if candidate_id in seen:
            continue
        seen.add(candidate_id)

        if any(
            alias in candidate
            for aliases in MARKET_ALIASES.values()
            for alias in aliases
        ):
            return candidate

        for rows_key in ("markets", "rows", "items", "data", "output"):
            normalized_rows = _market_from_rows(candidate.get(rows_key))
            if normalized_rows:
                merged = dict(candidate)
                merged.update(normalized_rows)
                return merged

        for key in wrapper_keys:
            value = candidate.get(key)
            if isinstance(value, dict):
                queue.append(value)

    return payload


def _normalize_entry(entry: Any, market_name: str) -> dict[str, Any]:
    source = entry if isinstance(entry, dict) else {}
    result = dict(source)
    for canonical, aliases in FIELD_ALIASES.items():
        value = _first(source, aliases)
        if value not in (None, ""):
            result[canonical] = value
    result.setdefault("market_name", market_name)
    return result


def normalize_market_supply(payload: Any) -> dict[str, Any]:
    container = _unwrap_market_supply(payload)
    result: dict[str, Any] = {}
    rows = _market_from_rows(
        container.get("markets") if isinstance(container, dict) else None
    )

    for canonical, aliases in MARKET_ALIASES.items():
        raw_entry = None
        if isinstance(container, dict):
            for alias in aliases:
                value = container.get(alias)
                if isinstance(value, dict):
                    raw_entry = value
                    break
        if raw_entry is None:
            raw_entry = rows.get(canonical)
        result[canonical] = _normalize_entry(
            raw_entry,
            "KOSPI" if canonical == "kospi" else "KOSDAQ",
        )

    if isinstance(container, dict):
        for key in META_KEYS:
            if key in container:
                result[key] = container.get(key)
    return result


def _entry_valid(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    market_index = _number(entry.get("market_index"))
    change_rate = _number(entry.get("market_change_rate"))
    advancers = _number(entry.get("advancers"))
    decliners = _number(entry.get("decliners"))
    return (
        market_index is not None
        and market_index > 0
        and change_rate is not None
        and advancers is not None
        and decliners is not None
        and (advancers > 0 or decliners > 0)
    )


def market_supply_valid(payload: Any) -> bool:
    normalized = normalize_market_supply(payload)
    return _entry_valid(normalized.get("kospi")) and _entry_valid(
        normalized.get("kosdaq")
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{threading.get_ident()}.tmp"
    )
    with _WRITE_LOCK:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _sorted_files(directory: Path, pattern: str) -> list[Path]:
    try:
        paths = [path for path in directory.glob(pattern) if path.is_file()]
    except OSError:
        return []
    return sorted(paths, key=_safe_mtime, reverse=True)


def _fallback_candidates(root: Path, runtime: Path) -> list[Path]:
    candidates = [
        runtime / "market_supply_last_valid.json",
        runtime / "market_supply.json",
    ]
    candidates.extend(_sorted_files(runtime, "market_supply_after_*.json"))
    candidates.extend(_sorted_files(runtime, "market_supply_before_*.json"))

    legacy_runtime = root / "data" / "runtime"
    candidates.extend(_sorted_files(legacy_runtime, "market_supply_after_*.json"))
    candidates.extend(_sorted_files(legacy_runtime, "market_supply_before_*.json"))
    candidates.append(legacy_runtime / "market_supply.json")

    assets = root / "docs" / "assets"
    candidates.extend(_sorted_files(assets, "stockboard_market_supply*.json"))
    candidates.extend(_sorted_files(assets, "market_supply*.json"))
    return candidates


def _load_last_valid(
    root: Path,
    runtime: Path,
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    seen: set[Path] = set()
    checked: list[str] = []
    for path in _fallback_candidates(root, runtime):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        checked.append(str(path))
        try:
            payload = _read_json(path)
            normalized = normalize_market_supply(payload)
            if market_supply_valid(normalized):
                return normalized, str(path), checked
        except Exception:
            continue
    return None, None, checked


def install(context_module: Any, base_module: Any) -> None:
    if getattr(context_module, "_market_supply_last_valid_patch_installed", False):
        return
    if not hasattr(context_module, "_runtime_context_payload"):
        raise AttributeError("runtime context provider module was not found")

    original = context_module._runtime_context_payload
    root = Path(
        getattr(base_module, "ROOT", Path(__file__).resolve().parents[1])
    )
    runtime = Path(
        getattr(
            base_module,
            "RUNTIME_DIR",
            root / "data" / "runtime" / "stockboard_v2",
        )
    )
    last_valid_path = runtime / "market_supply_last_valid.json"

    def patched_runtime_context_payload() -> dict[str, Any]:
        try:
            raw_payload = original()
        except Exception as error:
            return {
                "schema_version": 1,
                "market_supply": {},
                "market_supply_display_basis": "ORIGINAL_CONTEXT_ERROR",
                "market_supply_display_source": None,
                "market_supply_patch_error": (
                    f"original context failed: {type(error).__name__}: {error}"
                ),
                "market_supply_patch_traceback": traceback.format_exc(limit=5),
            }

        payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
        raw_market_supply = payload.get("market_supply")

        try:
            normalized = normalize_market_supply(raw_market_supply)
            payload["market_supply_original_keys"] = (
                sorted(str(key) for key in raw_market_supply.keys())[:50]
                if isinstance(raw_market_supply, dict)
                else []
            )

            if market_supply_valid(normalized):
                payload["market_supply"] = normalized
                try:
                    snapshot = dict(normalized)
                    snapshot["last_valid_saved_at"] = payload.get("ts")
                    _atomic_write_json(last_valid_path, snapshot)
                except Exception as write_error:
                    payload["market_supply_last_valid_write_error"] = (
                        f"{type(write_error).__name__}: {write_error}"
                    )
                payload["market_supply_display_basis"] = "CURRENT_VALID"
                payload["market_supply_display_source"] = str(
                    normalized.get("source")
                    or normalized.get("source_file")
                    or "runtime_current"
                )
                payload["market_supply_patch_error"] = None
                return payload

            fallback, source, checked = _load_last_valid(root, runtime)
            payload["market_supply_fallback_checked"] = checked
            if fallback is not None:
                payload["market_supply"] = fallback
                payload["market_supply_display_basis"] = "LAST_VALID_HOLD"
                payload["market_supply_display_source"] = source
                payload["market_supply_invalid_current"] = True
                try:
                    _atomic_write_json(last_valid_path, fallback)
                except Exception as write_error:
                    payload["market_supply_last_valid_write_error"] = (
                        f"{type(write_error).__name__}: {write_error}"
                    )
            else:
                payload["market_supply"] = normalized
                payload["market_supply_display_basis"] = "UNAVAILABLE"
                payload["market_supply_display_source"] = None
            payload["market_supply_patch_error"] = None
            return payload
        except Exception as error:
            # Context API must never terminate its request thread because of this
            # optional display fallback. Return the original payload with diagnostics.
            payload["market_supply_display_basis"] = "PATCH_FAIL_OPEN"
            payload["market_supply_display_source"] = None
            payload["market_supply_patch_error"] = (
                f"{type(error).__name__}: {error}"
            )
            payload["market_supply_patch_traceback"] = traceback.format_exc(limit=8)
            return payload

    context_module._runtime_context_payload = patched_runtime_context_payload
    context_module._market_supply_last_valid_patch_installed = True
    context_module._market_supply_original_context_payload = original
