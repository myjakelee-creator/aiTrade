from __future__ import annotations

import json
import threading
import traceback
from collections import deque
from pathlib import Path
from typing import Any, Iterator


MARKET_ALIASES = {
    "kospi": ("kospi", "KOSPI", "Kospi", "ks", "KS", "0", "001"),
    "kosdaq": ("kosdaq", "KOSDAQ", "Kosdaq", "kq", "KQ", "1", "101"),
}

MARKET_NAME_KEYS = (
    "market_name",
    "market",
    "name",
    "label",
    "시장",
    "mrkt_tp",
    "market_type",
    "inds_cd",
    "index_code",
)

FIELD_ALIASES = {
    "market_name": MARKET_NAME_KEYS,
    "market_index": (
        "market_index",
        "index",
        "index_value",
        "지수",
        "cur_prc",
        "current_index",
        "stck_prpr",
    ),
    "market_change_rate": (
        "market_change_rate",
        "change_rate",
        "change_percent",
        "등락률",
        "flu_rt",
        "rate",
        "prdy_ctrt",
    ),
    "advancers": (
        "advancers",
        "advance",
        "advance_count",
        "상승",
        "rising",
        "up_count",
        "rise_count",
    ),
    "upper_limit_count": (
        "upper_limit_count",
        "upper_limit",
        "상한",
        "upl",
        "upper_count",
    ),
    "decliners": (
        "decliners",
        "decline",
        "decline_count",
        "하락",
        "fall",
        "down_count",
        "fall_count",
    ),
    "lower_limit_count": (
        "lower_limit_count",
        "lower_limit",
        "하한",
        "lst",
        "lower_count",
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

CORE_FIELDS = (
    "market_index",
    "market_change_rate",
    "advancers",
    "decliners",
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


def _market_kind(value: Any) -> str | None:
    text = str(value or "").strip().upper().replace(" ", "")
    if not text:
        return None
    if "KOSDAQ" in text or text in {
        "1",
        "101",
        "KQ",
        "P10102",
        "P101_AL02",
        "코스닥",
    }:
        return "kosdaq"
    if "KOSPI" in text or text in {
        "0",
        "001",
        "KS",
        "P00101",
        "P001_AL01",
        "코스피",
    }:
        return "kospi"
    return None


def _normalize_entry(entry: Any, market_name: str) -> dict[str, Any]:
    source = entry if isinstance(entry, dict) else {}
    result = dict(source)
    for canonical, aliases in FIELD_ALIASES.items():
        value = _first(source, aliases)
        if value not in (None, ""):
            result[canonical] = value
    result.setdefault("market_name", market_name)
    return result


def _entry_score(entry: Any) -> int:
    if not isinstance(entry, dict):
        return -1
    normalized = _normalize_entry(entry, "")
    score = 0
    for key in CORE_FIELDS:
        if _number(normalized.get(key)) is not None:
            score += 10
    for key in (
        "upper_limit_count",
        "lower_limit_count",
        "individual_eok",
        "foreign_spot_eok",
        "institution_eok",
        "program_market_eok",
    ):
        if _number(normalized.get(key)) is not None:
            score += 1
    if normalized.get("available") is True:
        score += 2
    return score


def _iter_nodes(payload: Any, max_nodes: int = 20_000) -> Iterator[tuple[str, Any]]:
    queue: deque[tuple[str, Any]] = deque([("$", payload)])
    seen: set[int] = set()
    count = 0
    while queue and count < max_nodes:
        path, node = queue.popleft()
        if isinstance(node, (dict, list)):
            node_id = id(node)
            if node_id in seen:
                continue
            seen.add(node_id)
        count += 1
        yield path, node
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, (dict, list)):
                    queue.append((f"{path}.{key}", child))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                if isinstance(child, (dict, list)):
                    queue.append((f"{path}[{index}]", child))


def _find_market_entries(payload: Any) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    best: dict[str, tuple[int, dict[str, Any], str]] = {}

    def consider(kind: str | None, entry: Any, path: str) -> None:
        if kind not in {"kospi", "kosdaq"} or not isinstance(entry, dict):
            return
        score = _entry_score(entry)
        previous = best.get(kind)
        if previous is None or score > previous[0]:
            best[kind] = (score, entry, path)

    for path, node in _iter_nodes(payload):
        if not isinstance(node, dict):
            continue

        for kind, aliases in MARKET_ALIASES.items():
            for alias in aliases:
                entry = node.get(alias)
                if isinstance(entry, dict):
                    consider(kind, entry, f"{path}.{alias}")

        explicit_kind = _market_kind(_first(node, MARKET_NAME_KEYS))
        consider(explicit_kind, node, path)

        for key, child in node.items():
            if isinstance(child, dict):
                consider(_market_kind(key), child, f"{path}.{key}")

    entries = {
        kind: _normalize_entry(value[1], "KOSPI" if kind == "kospi" else "KOSDAQ")
        for kind, value in best.items()
    }
    paths = {kind: value[2] for kind, value in best.items()}
    return entries, paths


def normalize_market_supply(payload: Any) -> dict[str, Any]:
    entries, paths = _find_market_entries(payload)
    result: dict[str, Any] = {
        "kospi": entries.get("kospi", {"market_name": "KOSPI"}),
        "kosdaq": entries.get("kosdaq", {"market_name": "KOSDAQ"}),
    }
    if isinstance(payload, dict):
        for key in META_KEYS:
            if key in payload:
                result[key] = payload.get(key)
    if paths:
        result["normalized_paths"] = paths
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


def _decode_json_bytes(raw: bytes) -> tuple[dict[str, Any] | None, str | None]:
    if not raw:
        return None, None

    encodings: list[str] = []
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings.append("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        encodings.append("utf-8-sig")
    encodings.extend(("utf-8-sig", "utf-16", "cp949"))

    tried: set[str] = set()
    for encoding in encodings:
        if encoding in tried:
            continue
        tried.add(encoding)
        try:
            payload = json.loads(raw.decode(encoding))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload, encoding
    return None, None


def _read_json_with_encoding(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return _decode_json_bytes(path.read_bytes())
    except OSError:
        return None, None


def _read_json(path: Path) -> dict[str, Any] | None:
    payload, _encoding = _read_json_with_encoding(path)
    return payload


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
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
) -> tuple[dict[str, Any] | None, str | None, list[dict[str, Any]]]:
    seen: set[Path] = set()
    diagnostics: list[dict[str, Any]] = []
    for path in _fallback_candidates(root, runtime):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)

        payload, encoding = _read_json_with_encoding(path)
        normalized = normalize_market_supply(payload)
        valid = market_supply_valid(normalized)
        diagnostics.append(
            {
                "path": str(path),
                "exists": path.is_file(),
                "encoding": encoding,
                "valid": valid,
                "normalized_paths": normalized.get("normalized_paths") or {},
            }
        )
        if valid:
            normalized["source_file"] = str(path)
            normalized["source_encoding"] = encoding
            return normalized, str(path), diagnostics
    return None, None, diagnostics


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

            fallback, source, diagnostics = _load_last_valid(root, runtime)
            payload["market_supply_fallback_diagnostics"] = diagnostics
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
