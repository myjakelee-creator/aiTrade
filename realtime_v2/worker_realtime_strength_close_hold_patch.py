from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.common import (
    RUNTIME_DIR,
    atomic_write_json,
    normalize_code,
    now_text,
    to_number,
    trading_date_text,
)
from realtime_v2.market_session import market_session_now, next_premarket_datetime

PATCH_VERSION = "realtime_strength_final_hold_v1"
LIVE_SOURCE = "kiwoom_rest_ws_0B_fid228"
HOLD_SOURCE = "kiwoom_rest_ws_0B_fid228_close_hold"
DEFAULT_SNAPSHOT_PATH = RUNTIME_DIR / "realtime_strength_final_hold.json"

PERSIST_KEYS = (
    "execution_strength",
    "execution_strength_source",
    "execution_strength_status",
    "execution_strength_updated_at",
    "execution_strength_received_at",
    "execution_strength_source_time",
    "execution_strength_exchange",
    "execution_strength_market_phase",
    "execution_strength_trade_price",
    "last_valid_execution_strength",
    "last_valid_strength_at",
)


def _number(value: Any) -> float | None:
    number = to_number(value)
    return None if number is None else float(number)


def _positive(value: Any) -> float | None:
    number = _number(value)
    if number is None or number <= 0:
        return None
    return number


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _entry_valid(entry: dict[str, Any], now: datetime | None = None) -> bool:
    if not isinstance(entry, dict):
        return False
    if str(entry.get("source") or "") not in {LIVE_SOURCE, HOLD_SOURCE}:
        return False
    if _positive(entry.get("execution_strength")) is None:
        return False
    expires_at = _parse_datetime(entry.get("expires_at"))
    return expires_at is not None and (now or datetime.now()) < expires_at


def _snapshot_path(config: dict[str, Any]) -> Path:
    ws_config = config.get("realtime_strength_ws")
    ws_config = ws_config if isinstance(ws_config, dict) else {}
    hold_config = ws_config.get("close_hold")
    hold_config = hold_config if isinstance(hold_config, dict) else {}
    filename = str(hold_config.get("snapshot_file") or "").strip()
    if not filename:
        return DEFAULT_SNAPSHOT_PATH
    candidate = Path(filename)
    return candidate if candidate.is_absolute() else RUNTIME_DIR / candidate


def _hold_config(config: dict[str, Any]) -> dict[str, Any]:
    ws_config = config.get("realtime_strength_ws")
    ws_config = ws_config if isinstance(ws_config, dict) else {}
    raw = ws_config.get("close_hold")
    return dict(raw) if isinstance(raw, dict) else {}


def _entry_from_values(
    code: str,
    values: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    code = normalize_code(code)
    if not code or not isinstance(values, dict):
        return None
    source = str(values.get("execution_strength_source") or "")
    strength = _positive(values.get("execution_strength"))
    if source not in {LIVE_SOURCE, HOLD_SOURCE} or strength is None:
        return None
    current = now or datetime.now()
    expires_at = next_premarket_datetime(current)
    received_at = (
        values.get("execution_strength_received_at")
        or values.get("execution_strength_updated_at")
        or now_text()
    )
    return {
        "stock_code": code,
        "execution_strength": round(strength, 4),
        "source": LIVE_SOURCE,
        "status": "captured_live_final",
        "source_time": values.get("execution_strength_source_time"),
        "received_at": received_at,
        "updated_at": values.get("execution_strength_updated_at") or received_at,
        "exchange": values.get("execution_strength_exchange"),
        "market_phase": values.get("execution_strength_market_phase"),
        "trade_price": values.get("execution_strength_trade_price"),
        "captured_at": now_text(),
        "captured_trading_date": trading_date_text(),
        "expires_at": expires_at.isoformat(timespec="seconds"),
    }


def _load_snapshot(path: Path, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(raw_values, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for raw_code, raw_entry in raw_values.items():
        code = normalize_code(raw_code)
        if code and isinstance(raw_entry, dict) and _entry_valid(raw_entry, now):
            result[code] = dict(raw_entry)
    return result


def _display_allowed(config: dict[str, Any], phase: str) -> bool:
    hold = _hold_config(config)
    phases = hold.get("display_phases")
    if not isinstance(phases, list):
        phases = ["closed", "before_market", "weekend", "holiday"]
    return bool(hold.get("enabled", True)) and phase in {str(value) for value in phases}


def install(base) -> None:
    """Persist the final valid FID228 value until the next premarket boundary.

    No thread, socket, REST request, QAx registration, or browser calculation is
    added. The latest valid S1 WebSocket value is copied into one small runtime JSON
    file at most once per configured save interval. Closed-session rows use that
    value when the live field is blank or zero. At the next configured premarket
    boundary the hold expires automatically.
    """

    state_class = getattr(base, "State", None)
    updater_class = getattr(base, "ProgramNetUpdater", None)
    if state_class is None or updater_class is None:
        return
    if getattr(state_class, "_stockboard_realtime_strength_close_hold_installed", False):
        return

    from realtime_v2.worker_rest_live_metrics_patch import _read_config

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *PERSIST_KEYS))
    )

    original_state_init = state_class.__init__
    original_apply = state_class.apply_realtime_strength_ws
    original_rows = state_class.rows
    original_persist = state_class.persist_daily_state_if_needed

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        config = _read_config()
        hold = _hold_config(config)
        path = _snapshot_path(config)
        now = datetime.now()
        cache = _load_snapshot(path, now)
        daily_merged = False

        for code, values in self.daily_values_by_code.items():
            entry = _entry_from_values(code, values, now=now)
            if entry is not None:
                cache[code] = entry
                daily_merged = True

        self.realtime_strength_close_hold_full_config = config
        self.realtime_strength_close_hold_config = hold
        self.realtime_strength_close_hold_path = path
        self.realtime_strength_close_hold_by_code = cache
        self.realtime_strength_close_hold_dirty = daily_merged or (
            bool(cache) and not path.is_file()
        )
        self.realtime_strength_close_hold_last_save_mono = 0.0
        with self.lock:
            self.status.update(
                {
                    "realtime_strength_close_hold_installed": True,
                    "realtime_strength_close_hold_version": PATCH_VERSION,
                    "realtime_strength_close_hold_enabled": bool(
                        hold.get("enabled", True)
                    ),
                    "realtime_strength_close_hold_path": str(path),
                    "realtime_strength_close_hold_loaded_count": len(cache),
                    "realtime_strength_close_hold_loaded_at": now_text(),
                }
            )

    def write_hold(self, force: bool = False) -> bool:
        hold = getattr(self, "realtime_strength_close_hold_config", {}) or {}
        if not bool(hold.get("enabled", True)):
            return False
        interval = max(1.0, float(hold.get("save_interval_sec") or 5))
        now_mono = time.monotonic()
        with self.lock:
            dirty = bool(getattr(self, "realtime_strength_close_hold_dirty", False))
            last_save = float(
                getattr(self, "realtime_strength_close_hold_last_save_mono", 0.0)
                or 0.0
            )
            if not force and (not dirty or now_mono - last_save < interval):
                return False
            now = datetime.now()
            cache = {
                code: deepcopy(entry)
                for code, entry in (
                    getattr(self, "realtime_strength_close_hold_by_code", {}) or {}
                ).items()
                if _entry_valid(entry, now)
            }
            path = getattr(
                self,
                "realtime_strength_close_hold_path",
                DEFAULT_SNAPSHOT_PATH,
            )

        saved_at = now_text()
        atomic_write_json(
            path,
            {
                "schema_version": 1,
                "source": "stockboard_realtime_strength_final_hold",
                "updated_at": saved_at,
                "count": len(cache),
                "values": cache,
            },
        )
        with self.lock:
            self.realtime_strength_close_hold_dirty = False
            self.realtime_strength_close_hold_last_save_mono = now_mono
            self.status["realtime_strength_close_hold_saved_count"] = len(cache)
            self.status["realtime_strength_close_hold_last_saved_at"] = saved_at
            self.status["realtime_strength_close_hold_last_error"] = None
        return True

    def apply_realtime_strength_ws(self, event: dict[str, Any]) -> bool:
        changed = original_apply(self, event)
        code = normalize_code(event.get("stock_code"))
        if not code:
            return changed
        with self.lock:
            quote = dict(self.quotes.get(code) or {})
        entry = _entry_from_values(code, quote)
        if entry is None:
            return changed
        with self.lock:
            cache = getattr(self, "realtime_strength_close_hold_by_code", None)
            if not isinstance(cache, dict):
                cache = {}
                self.realtime_strength_close_hold_by_code = cache
            cache[code] = entry
            self.realtime_strength_close_hold_dirty = True
            self.status["realtime_strength_close_hold_last_code"] = code
            self.status["realtime_strength_close_hold_last_value"] = entry.get(
                "execution_strength"
            )
            self.status["realtime_strength_close_hold_last_captured_at"] = entry.get(
                "captured_at"
            )
            self.status["realtime_strength_close_hold_expires_at"] = entry.get(
                "expires_at"
            )
        try:
            write_hold(self, force=False)
        except Exception as error:
            with self.lock:
                self.status["realtime_strength_close_hold_last_error"] = (
                    f"{type(error).__name__}: {error}"
                )
        return changed

    def rows(self, limit: int = 300):
        result = original_rows(self, limit)
        config = getattr(self, "realtime_strength_close_hold_full_config", {}) or {}
        session = market_session_now()
        phase = str(session.phase or "")
        allowed = _display_allowed(config, phase)
        now = datetime.now()
        with self.lock:
            cache = {
                code: deepcopy(entry)
                for code, entry in (
                    getattr(self, "realtime_strength_close_hold_by_code", {}) or {}
                ).items()
                if _entry_valid(entry, now)
            }

        displayed = 0
        if allowed:
            for row in result:
                if not isinstance(row, dict):
                    continue
                code = normalize_code(row.get("stock_code"))
                entry = cache.get(code)
                if entry is None:
                    continue
                strength = _positive(entry.get("execution_strength"))
                if strength is None:
                    continue
                row.update(
                    {
                        "execution_strength": round(strength, 4),
                        "last_valid_execution_strength": round(strength, 4),
                        "execution_strength_source": HOLD_SOURCE,
                        "execution_strength_status": "market_closed_final_hold",
                        "execution_strength_available": True,
                        "execution_strength_live": False,
                        "execution_strength_display_basis": (
                            "final_fid228_until_next_premarket"
                        ),
                        "execution_strength_updated_at": entry.get("updated_at"),
                        "execution_strength_received_at": entry.get("received_at"),
                        "execution_strength_source_time": entry.get("source_time"),
                        "execution_strength_exchange": entry.get("exchange"),
                        "execution_strength_market_phase": entry.get("market_phase"),
                        "execution_strength_trade_price": entry.get("trade_price"),
                        "execution_strength_hold_until": entry.get("expires_at"),
                    }
                )
                displayed += 1

        with self.lock:
            self.status["realtime_strength_close_hold_phase"] = phase
            self.status["realtime_strength_close_hold_display_active"] = allowed
            self.status["realtime_strength_close_hold_displayed_rows"] = displayed
            self.status["realtime_strength_close_hold_valid_count"] = len(cache)

        if allowed and getattr(self, "realtime_strength_close_hold_dirty", False):
            try:
                write_hold(self, force=True)
            except Exception as error:
                with self.lock:
                    self.status["realtime_strength_close_hold_last_error"] = (
                        f"{type(error).__name__}: {error}"
                    )
        return result

    def persist_daily_state_if_needed(self, force: bool = False) -> bool:
        try:
            write_hold(self, force=force)
        except Exception as error:
            with self.lock:
                self.status["realtime_strength_close_hold_last_error"] = (
                    f"{type(error).__name__}: {error}"
                )
        return original_persist(self, force)

    state_class.__init__ = state_init
    state_class.apply_realtime_strength_ws = apply_realtime_strength_ws
    state_class.rows = rows
    state_class.persist_daily_state_if_needed = persist_daily_state_if_needed
    state_class.flush_realtime_strength_close_hold = write_hold
    state_class._stockboard_realtime_strength_close_hold_installed = True

    original_updater_class = updater_class

    class ProgramNetUpdaterWithStrengthCloseHold(original_updater_class):
        _stockboard_realtime_strength_close_hold_installed = True

        def stop(self) -> None:
            try:
                self.state.flush_realtime_strength_close_hold(force=True)
            finally:
                super().stop()

    base.ProgramNetUpdater = ProgramNetUpdaterWithStrengthCloseHold
