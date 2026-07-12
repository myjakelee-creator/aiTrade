from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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
    return isinstance(payload, dict) and _entry_valid(payload.get("kospi")) and _entry_valid(payload.get("kosdaq"))


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


def _fallback_candidates(root: Path, runtime: Path) -> list[Path]:
    candidates = [runtime / "market_supply_last_valid.json"]
    candidates.extend(sorted(runtime.glob("market_supply_after_*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    candidates.extend(sorted(runtime.glob("market_supply_before_*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    legacy_runtime = root / "data" / "runtime"
    candidates.extend(sorted(legacy_runtime.glob("market_supply_after_*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    candidates.extend(sorted(legacy_runtime.glob("market_supply_before_*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    assets = root / "docs" / "assets"
    candidates.extend(sorted(assets.glob("stockboard_market_supply*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    candidates.extend(sorted(assets.glob("market_supply*.json"), key=lambda path: path.stat().st_mtime, reverse=True))
    return candidates


def _load_last_valid(root: Path, runtime: Path) -> tuple[dict[str, Any] | None, str | None]:
    seen: set[Path] = set()
    for path in _fallback_candidates(root, runtime):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = _read_json(path)
        if market_supply_valid(payload):
            return payload, str(path)
    return None, None


def install(large_module, base_module) -> None:
    if getattr(large_module, "_market_supply_last_valid_patch_installed", False):
        return

    original = large_module._runtime_context_payload
    root = Path(getattr(base_module, "ROOT", Path(__file__).resolve().parents[1]))
    runtime = Path(getattr(base_module, "RUNTIME_DIR", root / "data" / "runtime" / "stockboard_v2"))
    last_valid_path = runtime / "market_supply_last_valid.json"

    def patched_runtime_context_payload() -> dict[str, Any]:
        payload = original()
        payload = payload if isinstance(payload, dict) else {}
        market_supply = payload.get("market_supply")

        if market_supply_valid(market_supply):
            try:
                snapshot = dict(market_supply)
                snapshot["last_valid_saved_at"] = payload.get("ts")
                _atomic_write_json(last_valid_path, snapshot)
            except Exception:
                pass
            payload["market_supply_display_basis"] = "CURRENT_VALID"
            payload["market_supply_display_source"] = str(
                market_supply.get("source") or market_supply.get("source_file") or "runtime_current"
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
            payload["market_supply_display_basis"] = "UNAVAILABLE"
            payload["market_supply_display_source"] = None
        return payload

    large_module._runtime_context_payload = patched_runtime_context_payload
    large_module._market_supply_last_valid_patch_installed = True
