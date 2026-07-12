from __future__ import annotations

import json
import os
import threading
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from realtime_v2.after_close_recovery import date_text, now_text
from realtime_v2.market_session import last_completed_trading_date, next_premarket_datetime

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "data" / "runtime" / "stockboard_v2" / "after_close_recovery_state.json"
STATE_VERSION = "after_close_recovery_state_v1"
RECOVERY_KEYS = (
    "minute_recovery_status",
    "minute_recovery_trade_status",
    "minute_recovery_display_text",
    "minute_recovery_error",
    "minute_recovery_snapshot_at",
    "minute_recovery_trading_date",
    "minute_recovery_market_scope",
    "minute_trade_value_1m_eok",
    "minute_trade_value_5m_eok",
    "minute_ohlc",
    "trade_value_1m_eok",
    "trade_value_5m_eok",
    "recovery_values",
    "recovery_terminal_status",
    "recovery_display_status",
    "source_metadata",
    "close_flow_sampler_trading_date",
    "close_flow_sampler_basis_time",
)


def parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def recovery_entry(row: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in RECOVERY_KEYS:
        if key in row and row.get(key) not in (None, ""):
            result[key] = deepcopy(row[key])
    return result


class AfterCloseRecoveryStateService(threading.Thread):
    def __init__(
        self,
        state,
        path: Path = STATE_FILE,
        *,
        flush_sec: float = 5.0,
        now_provider=None,
    ):
        super().__init__(name="after-close-recovery-state", daemon=True)
        self.state = state
        self.path = Path(path)
        self.flush_sec = max(1.0, float(flush_sec))
        self.now_provider = now_provider or datetime.now
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.RLock()
        self.entries: dict[str, dict[str, Any]] = {}
        self.dirty = False
        self.last_saved_at = None
        self.last_error = None
        self.save_count = 0
        self.loaded_count = 0
        self.valid_until = None
        self.target_trading_date = None

    def mark(self, code: str, row: dict[str, Any] | None = None):
        if not code:
            return
        if row is None:
            lock = getattr(self.state, "lock", None)
            if lock is None:
                return
            with lock:
                row = deepcopy((getattr(self.state, "quotes", {}) or {}).get(code))
        if not isinstance(row, dict):
            return
        entry = recovery_entry(row)
        if not entry:
            return
        with self.lock:
            if self.entries.get(code) == entry:
                return
            self.entries[code] = entry
            self.dirty = True
        self.wake.set()

    def mark_codes(self, codes):
        lock = getattr(self.state, "lock", None)
        if lock is None:
            return
        with lock:
            rows = {
                str(code): deepcopy((getattr(self.state, "quotes", {}) or {}).get(str(code)))
                for code in codes
            }
        for code, row in rows.items():
            self.mark(code, row)

    def stop(self):
        self.stop_event.set()
        self.wake.set()

    def _payload(self):
        now = self.now_provider()
        valid_until = next_premarket_datetime(now)
        target_date = date_text(last_completed_trading_date(now))
        self.valid_until = valid_until.isoformat(timespec="seconds")
        self.target_trading_date = target_date
        return {
            "schema_version": 1,
            "source": STATE_VERSION,
            "target_trading_date": target_date,
            "saved_at": now.isoformat(timespec="seconds"),
            "valid_until": self.valid_until,
            "entry_count": len(self.entries),
            "entries": deepcopy(self.entries),
        }

    def save(self, force=False):
        with self.lock:
            if not force and not self.dirty:
                return False
            payload = self._payload()
            self.dirty = False
        if not payload["entries"]:
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
            self.last_saved_at = payload["saved_at"]
            self.last_error = None
            self.save_count += 1
            return True
        except OSError as error:
            with self.lock:
                self.dirty = True
            self.last_error = str(error)
            return False

    def status(self):
        with self.lock:
            return {
                "version": STATE_VERSION,
                "alive": self.is_alive(),
                "entry_count": len(self.entries),
                "dirty": self.dirty,
                "save_count": self.save_count,
                "loaded_count": self.loaded_count,
                "last_saved_at": self.last_saved_at,
                "valid_until": self.valid_until,
                "target_trading_date": self.target_trading_date,
                "last_error": self.last_error,
                "path": str(self.path),
            }

    def run(self):
        while not self.stop_event.is_set():
            self.wake.wait(self.flush_sec)
            self.wake.clear()
            if self.stop_event.is_set():
                break
            self.save()
            status = getattr(self.state, "status", None)
            if isinstance(status, dict):
                status["after_close_recovery_state"] = self.status()
        self.save(force=True)


def load_state(path: Path, now: datetime) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        return {}
    valid_until = parse_iso(payload.get("valid_until"))
    if valid_until is None or now >= valid_until:
        try:
            path.unlink()
        except OSError:
            pass
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    result = {}
    for raw_code, entry in entries.items():
        code = "".join(ch for ch in str(raw_code or "") if ch.isdigit())[:6]
        if len(code) == 6 and isinstance(entry, dict):
            result[code] = deepcopy(entry)
    return result


def install(base) -> None:
    if getattr(base, "_after_close_recovery_state_installed", False):
        return
    State = base.State
    original_state_init = State.__init__
    original_quote = State._quote
    original_close = State._apply_close_metrics
    original_server_init = base.WebServer.__init__
    original_server_close = base.WebServer.server_close

    def state_init(self, universe_file):
        original_state_init(self, universe_file)
        restored = load_state(STATE_FILE, datetime.now())
        for code, entry in restored.items():
            self.daily_values_by_code.setdefault(code, {}).update(deepcopy(entry))
        self.after_close_recovery_restored_count = len(restored)
        self.status["after_close_recovery_restored_count"] = len(restored)

    def quote_method(self, code):
        row = original_quote(self, code)
        entry = self.daily_values_by_code.get(code) or {}
        for key in RECOVERY_KEYS:
            if key in entry and key not in row:
                row[key] = deepcopy(entry[key])
        return row

    def close_method(self, event):
        original_close(self, event)
        values = base.merged_event_values(event)
        code = base.normalize_code(event.get("stock_code") or values.get("stock_code"))
        service = getattr(self, "after_close_recovery_state_service", None)
        if code and service:
            service.mark(code)

    def server_init(self, address, handler, state):
        original_server_init(self, address, handler, state)
        service = AfterCloseRecoveryStateService(state)
        self.after_close_recovery_state_service = service
        state.after_close_recovery_state_service = service
        with state.lock:
            for code in state.daily_values_by_code:
                row = state.quotes.get(code)
                if row is None:
                    row = state._quote(code)
                service.mark(code, deepcopy(row))
        service.loaded_count = int(
            getattr(state, "after_close_recovery_restored_count", 0) or 0
        )
        service.start()

    def server_close(self):
        service = getattr(self, "after_close_recovery_state_service", None)
        if service:
            service.stop()
            service.join(timeout=3)
        return original_server_close(self)

    State.__init__ = state_init
    State._quote = quote_method
    State._apply_close_metrics = close_method
    base.WebServer.__init__ = server_init
    base.WebServer.server_close = server_close
    base._after_close_recovery_state_installed = True
