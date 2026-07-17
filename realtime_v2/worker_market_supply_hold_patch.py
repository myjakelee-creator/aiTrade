from __future__ import annotations

import json
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.common import RUNTIME_DIR, atomic_write_json, now_text, to_number
from realtime_v2.market_session import last_completed_trading_date, market_session_now

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "stockboard_market_context.json"
PATCH_VERSION = "market_supply_hold_v2"

MARKET_ALIASES = {
    "kospi": ("kospi", "KOSPI"),
    "kosdaq": ("kosdaq", "KOSDAQ"),
}
FIELD_ALIASES = {
    "market_index": ("market_index", "index", "지수"),
    "market_change_rate": ("market_change_rate", "change_rate", "등락률"),
    "advancers": ("advancers", "advance", "상승"),
    "decliners": ("decliners", "decline", "하락"),
    "individual_eok": ("individual_eok", "individual", "개인"),
    "foreign_spot_eok": ("foreign_spot_eok", "foreign", "외인"),
    "institution_eok": ("institution_eok", "institution", "기관"),
    "program_market_eok": ("program_market_eok", "program", "프로"),
}
EXPLICIT_DATE_KEYS = (
    "source_trading_date",
    "trading_date",
    "target_date",
    "flow_date",
    "query_date",
    "business_date",
    "market_date",
    "date",
)
ACTIVE_CURRENT_DAY_PHASES = {
    "premarket",
    "opening_call",
    "regular",
    "closing_call",
    "after_wait",
    "aftermarket",
}


def _load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("market context config must be an object")
    raw = payload.get("market_supply_hold")
    raw = raw if isinstance(raw, dict) else {}
    candidate_paths = raw.get("candidate_paths")
    if not isinstance(candidate_paths, list) or not candidate_paths:
        raise ValueError("market_supply_hold.candidate_paths must be a non-empty list")
    required_markets = raw.get("required_markets")
    if not isinstance(required_markets, list) or not required_markets:
        raise ValueError("market_supply_hold.required_markets must be a non-empty list")
    return {
        "enabled": bool(raw.get("enabled", True)),
        "candidate_paths": [str(value) for value in candidate_paths if str(value).strip()],
        "required_markets": [str(value).strip().lower() for value in required_markets],
        "persist_filename": str(
            raw.get("persist_filename") or "market_supply_last_good_{trading_date}.json"
        ),
        "allow_previous_until_current_valid": bool(
            raw.get("allow_previous_until_current_valid", True)
        ),
        "replace_only_with_valid_current_day": bool(
            raw.get("replace_only_with_valid_current_day", True)
        ),
    }


def _date_digits(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _first(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _market_supply_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("market_supply")
    return nested if isinstance(nested, dict) else payload


def _market_row(payload: dict[str, Any], market: str) -> dict[str, Any] | None:
    aliases = MARKET_ALIASES.get(market, (market, market.upper()))
    for key in aliases:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in aliases:
        value = lowered.get(str(key).lower())
        if isinstance(value, dict):
            return value
    return None


def validate_market_supply(
    payload: Any,
    required_markets: tuple[str, ...] | list[str] = ("kospi", "kosdaq"),
) -> tuple[bool, str | None]:
    market_supply = _market_supply_payload(payload)
    if not market_supply:
        return False, "market_supply_not_object"

    for market in required_markets:
        row = _market_row(market_supply, str(market).lower())
        if not isinstance(row, dict):
            return False, f"missing_market:{market}"

        index_value = _number(_first(row, FIELD_ALIASES["market_index"]))
        if index_value is None or index_value <= 0:
            return False, f"missing_or_invalid_index:{market}"

        change_rate = _number(_first(row, FIELD_ALIASES["market_change_rate"]))
        if change_rate is None:
            return False, f"missing_change_rate:{market}"

        for field in ("advancers", "decliners"):
            value = _number(_first(row, FIELD_ALIASES[field]))
            if value is None or value < 0:
                return False, f"missing_or_invalid_{field}:{market}"

        for field in (
            "individual_eok",
            "foreign_spot_eok",
            "institution_eok",
            "program_market_eok",
        ):
            value = _number(_first(row, FIELD_ALIASES[field]))
            if value is None:
                return False, f"missing_{field}:{market}"

    return True, None


def _payload_date_info(payload: dict[str, Any]) -> tuple[str, str | None]:
    containers: list[tuple[str, dict[str, Any]]] = [("root", payload)]
    nested_market_supply = payload.get("market_supply")
    if isinstance(nested_market_supply, dict):
        containers.append(("market_supply", nested_market_supply))
    for key in ("meta", "status", "summary", "_status"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append((key, value))
        if isinstance(nested_market_supply, dict):
            nested_value = nested_market_supply.get(key)
            if isinstance(nested_value, dict):
                containers.append((f"market_supply.{key}", nested_value))
    market_supply = _market_supply_payload(payload)
    for market in ("kospi", "kosdaq"):
        row = _market_row(market_supply, market)
        if isinstance(row, dict):
            containers.append((market, row))

    for key in EXPLICIT_DATE_KEYS:
        for container_name, container in containers:
            date_text = _date_digits(container.get(key))
            if date_text:
                return date_text, f"{container_name}.{key}"
    return "", None


def _payload_date(payload: dict[str, Any]) -> str:
    return _payload_date_info(payload)[0]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _phase_context(now: datetime) -> tuple[dict[str, Any], str, bool]:
    session = market_session_now(now).to_dict()
    phase = str(session.get("phase") or "")
    current_date = _date_digits(session.get("trading_date") or session.get("calendar_date"))
    if phase in ACTIVE_CURRENT_DAY_PHASES:
        return session, current_date, True
    completed = _date_digits(last_completed_trading_date(now))
    return session, completed or current_date, False


class MarketSupplyHold:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        root: Path = ROOT,
        runtime_dir: Path = RUNTIME_DIR,
    ) -> None:
        self.config = config or _load_config()
        self.root = Path(root)
        self.runtime_dir = Path(runtime_dir)
        self.lock = threading.RLock()
        self._last_good: dict[str, Any] | None = None
        self._loaded = False
        self._last_saved_signature: str | None = None

    def _candidate_paths(self) -> list[Path]:
        result = []
        for raw in self.config.get("candidate_paths") or []:
            path = Path(str(raw))
            result.append(path if path.is_absolute() else self.root / path)
        return result

    def _persist_path(self, trading_date: str) -> Path:
        template = str(
            self.config.get("persist_filename")
            or "market_supply_last_good_{trading_date}.json"
        )
        return self.runtime_dir / template.format(trading_date=trading_date)

    def _persist_pattern(self) -> str:
        template = str(
            self.config.get("persist_filename")
            or "market_supply_last_good_{trading_date}.json"
        )
        return template.replace("{trading_date}", "*")

    def _load_latest_persisted(self) -> dict[str, Any] | None:
        if self._loaded:
            return self._last_good
        self._loaded = True
        try:
            paths = list(self.runtime_dir.glob(self._persist_pattern()))
        except OSError:
            paths = []
        paths.sort(
            key=lambda path: (
                _date_digits(path.stem),
                path.stat().st_mtime if path.exists() else 0,
            ),
            reverse=True,
        )
        for path in paths:
            payload = _read_json(path)
            market_supply = payload.get("market_supply") if isinstance(payload, dict) else None
            valid, _reason = validate_market_supply(
                market_supply, self.config.get("required_markets") or ("kospi", "kosdaq")
            )
            trading_date = _date_digits(payload.get("trading_date") if payload else None)
            if valid and trading_date:
                self._last_good = dict(payload)
                self._last_saved_signature = self._signature(trading_date, market_supply)
                return self._last_good
        return None

    @staticmethod
    def _signature(trading_date: str, market_supply: Any) -> str:
        return f"{trading_date}:" + json.dumps(
            market_supply, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    def _save_last_good(
        self,
        market_supply: dict[str, Any],
        trading_date: str,
        source_path: str,
        source_mtime: float | None,
        date_source: str | None = None,
    ) -> dict[str, Any]:
        signature = self._signature(trading_date, market_supply)
        if signature == self._last_saved_signature and self._last_good is not None:
            return self._last_good
        payload = {
            "schema_version": 1,
            "source": "stockboard_v2_market_supply_last_good",
            "patch_version": PATCH_VERSION,
            "trading_date": trading_date,
            "updated_at": now_text(),
            "source_path": source_path,
            "source_mtime": source_mtime,
            "source_date_field": date_source,
            "market_supply": deepcopy(market_supply),
        }
        atomic_write_json(self._persist_path(trading_date), payload)
        self._last_good = payload
        self._loaded = True
        self._last_saved_signature = signature
        return payload

    def _scan_candidates(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        valid_items: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for priority, path in enumerate(self._candidate_paths()):
            if not path.is_file():
                continue
            payload = _read_json(path)
            market_supply = _market_supply_payload(payload)
            valid, reason = validate_market_supply(
                market_supply, self.config.get("required_markets") or ("kospi", "kosdaq")
            )
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            date_text, date_source = _payload_date_info(payload or {})
            if valid and not date_text:
                reason = "missing_explicit_trading_date"
            item = {
                "market_supply": market_supply,
                "trading_date": date_text,
                "date_source": date_source,
                "source_path": str(path),
                "source_mtime": mtime,
                "priority": priority,
                "reason": reason,
            }
            if valid and date_text:
                valid_items.append(item)
            else:
                rejected.append(item)
        valid_items.sort(
            key=lambda item: (
                str(item.get("trading_date") or ""),
                float(item.get("source_mtime") or 0.0),
                -int(item.get("priority") or 0),
            ),
            reverse=True,
        )
        return valid_items, rejected

    def resolve(
        self,
        original_market_supply: Any,
        *,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        current = now or datetime.now()
        session, expected_date, current_day_phase = _phase_context(current)
        phase = str(session.get("phase") or "")

        with self.lock:
            persisted = self._load_latest_persisted()
            valid_items, rejected = self._scan_candidates()
            exact_matches = [
                item for item in valid_items if item.get("trading_date") == expected_date
            ]
            exact = exact_matches[0] if exact_matches else None
            chosen_payload: dict[str, Any] | None = None
            display_basis = "no_valid_market_supply"
            source_path = None
            source_date = None
            source_date_field = None
            last_good_at = persisted.get("updated_at") if isinstance(persisted, dict) else None

            if exact is not None:
                chosen_payload = self._save_last_good(
                    exact["market_supply"],
                    str(exact["trading_date"]),
                    str(exact["source_path"]),
                    float(exact.get("source_mtime") or 0.0),
                    str(exact.get("date_source") or "") or None,
                )
                display_basis = (
                    "live"
                    if phase not in {"closed", "before_market", "weekend", "holiday"}
                    else "after_close_hold"
                )
                source_path = exact.get("source_path")
                source_date = exact.get("trading_date")
                source_date_field = exact.get("date_source")
                last_good_at = chosen_payload.get("updated_at")
            else:
                held = persisted
                if held is None and current_day_phase and self.config.get(
                    "allow_previous_until_current_valid", True
                ):
                    previous_item = next(
                        (
                            item
                            for item in valid_items
                            if str(item.get("trading_date") or "") < expected_date
                        ),
                        None,
                    )
                    if previous_item is not None:
                        held = self._save_last_good(
                            previous_item["market_supply"],
                            str(previous_item["trading_date"]),
                            str(previous_item["source_path"]),
                            float(previous_item.get("source_mtime") or 0.0),
                            str(previous_item.get("date_source") or "") or None,
                        )

                held_date = _date_digits(
                    held.get("trading_date") if isinstance(held, dict) else None
                )
                allow_previous = bool(
                    current_day_phase
                    and self.config.get("allow_previous_until_current_valid", True)
                    and held_date
                    and held_date < expected_date
                )
                exact_hold = bool(held_date and held_date == expected_date)
                if isinstance(held, dict) and (exact_hold or allow_previous):
                    chosen_payload = held
                    source_path = held.get("source_path")
                    source_date = held_date
                    source_date_field = held.get("source_date_field")
                    last_good_at = held.get("updated_at")
                    if current_day_phase and allow_previous:
                        display_basis = (
                            "premarket_previous_hold"
                            if phase in {"premarket", "opening_call"}
                            else "current_session_previous_hold"
                        )
                    else:
                        display_basis = "after_close_hold"

            if chosen_payload is not None:
                market_supply = deepcopy(chosen_payload.get("market_supply") or {})
            else:
                market_supply = (
                    deepcopy(original_market_supply)
                    if isinstance(original_market_supply, dict)
                    else {}
                )

            first_reject = rejected[0] if rejected else None
            candidate_date_sources = [
                {
                    "path": item.get("source_path"),
                    "trading_date": item.get("trading_date"),
                    "date_source": item.get("date_source"),
                }
                for item in valid_items
            ]
            status = {
                "patch_version": PATCH_VERSION,
                "market_phase": phase,
                "expected_trading_date": expected_date or None,
                "display_basis": display_basis,
                "source_trading_date": source_date,
                "source_date_field": source_date_field,
                "last_good_at": last_good_at,
                "source_path": source_path,
                "candidate_valid": exact is not None,
                "candidate_exact_match_count": len(exact_matches),
                "candidate_source_path": exact.get("source_path") if exact else None,
                "candidate_reject_reason": first_reject.get("reason") if first_reject else None,
                "candidate_reject_path": first_reject.get("source_path") if first_reject else None,
                "candidate_valid_count": len(valid_items),
                "candidate_rejected_count": len(rejected),
                "candidate_trading_dates": sorted(
                    {str(item.get("trading_date") or "") for item in valid_items if item.get("trading_date")}
                ),
                "candidate_date_sources": candidate_date_sources,
                "persist_path": str(self._persist_path(source_date)) if source_date else None,
            }
            return market_supply, status


def install() -> None:
    """Install read-only market-supply last-good display protection.

    Existing context polling is reused. No request, SSE field, QAx registration, WebSocket,
    worker thread, browser calculation, or periodic timer is added.
    """

    from realtime_v2 import worker64_guarded as guarded

    if getattr(guarded, "_market_supply_hold_patch_installed", False):
        return

    original_context = getattr(guarded, "_runtime_context_payload", None)
    if not callable(original_context):
        raise AttributeError(
            "realtime_v2.worker64_guarded._runtime_context_payload is unavailable"
        )

    holder = MarketSupplyHold()

    def patched_runtime_context_payload() -> dict[str, Any]:
        payload = original_context()
        payload = dict(payload) if isinstance(payload, dict) else {}
        market_supply, status = holder.resolve(payload.get("market_supply"))
        payload["market_supply"] = market_supply
        payload["market_supply_status"] = status
        return payload

    guarded._runtime_context_payload = patched_runtime_context_payload
    guarded._market_supply_hold = holder
    guarded._market_supply_hold_patch_installed = True
