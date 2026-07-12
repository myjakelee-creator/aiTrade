from __future__ import annotations

import json
from pathlib import Path
from typing import Any


MARKET_ALIASES = {
    "kospi": ("kospi", "KOSPI", "Kospi", "ks", "KS", "0", "001"),
    "kosdaq": ("kosdaq", "KOSDAQ", "Kosdaq", "kq", "KQ", "1", "101"),
}

FIELD_ALIASES = {
    "market_name": ("market_name", "market", "name", "label", "시장"),
    "market_index": ("market_index", "index", "지수", "cur_prc", "current_index"),
    "market_change_rate": (
        "market_change_rate",
        "change_rate",
        "등락률",
        "flu_rt",
        "rate",
    ),
    "advancers": ("advancers", "advance", "상승", "rising", "up_count"),
    "upper_limit_count": (
        "upper_limit_count",
        "upper_limit",
        "상한",
        "upl",
    ),
    "decliners": ("decliners", "decline", "하락", "fall", "down_count"),
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
        if "KOSDAQ" in name or name in {"1", "101", "KQ", "P10102", "P101_AL02"}:
            result["kosdaq"] = row
        elif "KOSPI" in name or name in {"0", "001", "KS", "P00101", "P001_AL01"}:
            result["kospi"] = row
    return result


def _unwrap_market_supply(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}

    candidates: list[dict[str, Any]] = [payload]
    for key in ("market_supply", "values", "result", "payload", "data"):
        value = payload.get(key)
        if isinstance(value, dict):
            candidates.append(value)

    for candidate in candidates:
        if any(alias in candidate for aliases in MARKET_ALIASES.values() for alias in aliases):
            return candidate
        for rows_key in ("markets", "rows", "items", "data", "output"):
            rows = candidate.get(rows_key)
            normalized_rows = _market_from_rows(rows)
            if normalized_rows:
                merged = dict(candidate)
                merged.update(normalized_rows)
                return merged

    normalized_rows = _market_from_rows(payload.get("markets"))
    return normalized_rows or payload


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

    for canonical, aliases in MARKET_ALIASES.items():
        raw_entry = None
        for alias in aliases:
            value = container.get(alias) if isinstance(container, dict) else None
            if isinstance(value, dict):
                raw_entry = value
                break
        if raw_entry is None:
            rows = _market_from_rows(
                container.get("markets")
                if isinstance(container, dict)
                else None
            )
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
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sorted_files(directory: Path, pattern: str) -> list[Path]:
    try:
        paths = [path for path in directory.glob(pattern) if path.is_file()]
    except OSError:
        return []
    return sorted(
        paths,
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )


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
    root: Path, runtime: Path
) -> tuple[dict[str, Any] | None, str | None]:
    seen: set[Path] = set()
    for path in _fallback_candidates(root, runtime):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = _read_json(path)
        normalized = normalize_market_supply(payload)
        if market_supply_valid(normalized):
            return normalized, str(path)
    return None, None


def _context_module(wrapper_module: Any) -> Any:
    if hasattr(wrapper_module, "_runtime_context_payload"):
        return wrapper_module
    guarded = getattr(wrapper_module, "guarded", None)
    if guarded is not None and hasattr(guarded, "_runtime_context_payload"):
        return guarded
    raise AttributeError("runtime context provider module was not found")


def install(wrapper_module: Any, base_module: Any) -> None:
    if getattr(wrapper_module, "_market_supply_last_valid_patch_installed", False):
        return

    context_module = _context_module(wrapper_module)
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
        payload = original()
        payload = payload if isinstance(payload, dict) else {}
        raw_market_supply = payload.get("market_supply")
        normalized = normalize_market_supply(raw_market_supply)

        payload["market_supply_original_keys"] = (
            sorted(str(key) for key in raw_market_supply.keys())[:30]
            if isinstance(raw_market_supply, dict)
            else []
        )

        if market_supply_valid(normalized):
            payload["market_supply"] = normalized
            try:
                snapshot = dict(normalized)
                snapshot["last_valid_saved_at"] = payload.get("ts")
                _atomic_write_json(last_valid_path, snapshot)
            except Exception:
                pass
            payload["market_supply_display_basis"] = "CURRENT_VALID"
            payload["market_supply_display_source"] = str(
                normalized.get("source")
                or normalized.get("source_file")
                or "runtime_current"
            )
            return payload

        fallback, source = _load_last_valid(root, runtime)
        if fallback is not None:
            payload["market_supply"] = fallback
            payload["market_supply_display_basis"] = "LAST_VALID_HOLD"
            payload["market_supply_display_source"] = source
            payload["market_supply_invalid_current"] = True
            try:
                _atomic_write_json(last_valid_path, fallback)
            except Exception:
                pass
        else:
            payload["market_supply"] = normalized
            payload["market_supply_display_basis"] = "UNAVAILABLE"
            payload["market_supply_display_source"] = None
        return payload

    context_module._runtime_context_payload = patched_runtime_context_payload
    wrapper_module._market_supply_last_valid_patch_installed = True
    wrapper_module._market_supply_context_module = context_module.__name__
