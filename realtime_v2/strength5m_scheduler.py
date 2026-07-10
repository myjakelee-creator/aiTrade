from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
SELECTED_PATH = ROOT / "data" / "runtime" / "stockboard_v2" / "selected_code.json"
COMMAND_RE = re.compile(r"^SBV2\|[^|]+\|(\d{6})$")


def _code(base, value: Any) -> str:
    return base.normalize_code(value) or ""


def _read_json_url(url: str) -> dict[str, Any]:
    with urlopen(url, timeout=1.5) as response:
        value = json.loads(response.read().decode("utf-8"))
    return value if isinstance(value, dict) else {}


def _clipboard_text() -> str:
    if os.name != "nt":
        return ""
    try:
        import ctypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(13)
            pointer = kernel32.GlobalLock(handle) if handle else None
            if not pointer:
                return ""
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def _load_selected(base) -> str:
    try:
        value = json.loads(SELECTED_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ""
    return _code(base, value.get("stock_code")) if isinstance(value, dict) else ""


def _save_selected(code: str) -> None:
    try:
        SELECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp = SELECTED_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps({"stock_code": code, "updated_at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, SELECTED_PATH)
    except OSError:
        pass


def _refresh_selected(base, current: str) -> str:
    match = COMMAND_RE.match(_clipboard_text().strip())
    code = _code(base, match.group(1)) if match else ""
    if code and code != current:
        _save_selected(code)
        return code
    return current


def _model_rank(row: dict[str, Any], fallback: int) -> int:
    for key in ("model_rank", "funnel_rank", "pool_rank"):
        try:
            value = int(float(row.get(key)))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return fallback


def build_lane_plan(base, payload: dict[str, Any], selected: str = "") -> list[dict[str, Any]]:
    """Preserve current Top20 membership; classify only query priority."""
    rows = [row for row in payload.get("rows", []) if isinstance(row, dict)]
    by_code = {_code(base, row.get("stock_code")): row for row in rows}
    result: list[dict[str, Any]] = []
    used: set[str] = set()

    def add(value: Any, lane: str, row: dict[str, Any] | None = None) -> None:
        code = _code(base, value)
        if code and code not in used:
            used.add(code)
            result.append({"stock_code": code, "lane": lane, "row": row or {}})

    selected = _code(base, selected)
    if selected:
        add(selected, "s1", by_code.get(selected))
    for row in rows[:20]:
        add(row.get("stock_code"), "top20", row)
    hidden = sorted(
        ((index, row) for index, row in enumerate(rows[20:], 21) if 21 <= _model_rank(row, index) <= 50),
        key=lambda item: (_model_rank(item[1], item[0]), item[0]),
    )
    for _index, row in hidden:
        add(row.get("stock_code"), "hidden50", row)
    for row in rows[20:]:
        add(row.get("stock_code"), "top300", row)
    return result


def _timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _age(row: dict[str, Any]) -> float | None:
    parsed = _timestamp(row.get("strength_completed_at") or row.get("strength_snapshot_at") or row.get("last_valid_strength_at"))
    return max(0.0, (datetime.now() - parsed).total_seconds()) if parsed else None


class Strength5mScheduler(threading.Thread):
    NORMAL = {"s1": 30.0, "top20": 90.0, "hidden50": 300.0, "top300": 1200.0}
    OPENING = {"s1": 45.0, "top20": 120.0, "hidden50": 600.0, "top300": 1800.0}

    def __init__(self, base, provider) -> None:
        super().__init__(name="StockBoardStrength5mScheduler", daemon=True)
        self.base, self.provider = base, provider
        self.stop_event = threading.Event()
        self.url = os.getenv("STOCKBOARD_V2_WORKER_SNAPSHOT_URL", "http://127.0.0.1:8765/api/v2/snapshot?limit=300")
        self.poll_sec = max(2.0, float(os.getenv("STOCKBOARD_STRENGTH_5M_SNAPSHOT_POLL_SEC", "5")))
        self.normal_gap = max(1.05, float(os.getenv("STOCKBOARD_STRENGTH_5M_GLOBAL_GAP_SEC", "1.25")))
        self.opening_gap = max(self.normal_gap, float(os.getenv("STOCKBOARD_STRENGTH_5M_OPENING_GAP_SEC", "2")))
        self.selected = _load_selected(base)
        self.payload: dict[str, Any] = {}
        self.last_poll = 0.0
        self.local_last: dict[str, float] = {}
        self.enqueue_count = self.busy_skips = self.snapshot_errors = 0
        self.last_code = self.last_lane = ""
        self.last_cycle_at = self.last_error = None

    @staticmethod
    def opening() -> bool:
        now = datetime.now()
        minute = now.hour * 60 + now.minute
        return 535 <= minute < 550

    def intervals(self) -> dict[str, float]:
        return self.OPENING if self.opening() else self.NORMAL

    def gap(self) -> float:
        return self.opening_gap if self.opening() else self.normal_gap

    def _refresh(self) -> None:
        self.selected = _refresh_selected(self.base, self.selected)
        now = time.monotonic()
        if now - self.last_poll < self.poll_sec:
            return
        self.last_poll = now
        try:
            self.payload = _read_json_url(self.url)
            self.last_error = None
        except Exception as error:
            self.snapshot_errors += 1
            self.last_error = f"snapshot: {error}"

    def _idle(self) -> bool:
        provider = self.provider
        lock = getattr(provider, "_lock", None)
        if lock is None:
            return False
        with lock:
            if any((getattr(provider, "_strength_probe_inflight", None), getattr(provider, "_orderbook_probe_inflight", None), getattr(provider, "_opt10055_probe_inflight", None))):
                return False
            queues = ("_strength_probe_pending", "_orderbook_probe_pending", "_opt10055_probe_pending", "_close_metrics_queue")
            if any(len(getattr(provider, name, ())) for name in queues):
                return False
            last = max(float(getattr(provider, name, 0.0) or 0.0) for name in ("_strength_probe_last_request_at", "_orderbook_probe_last_request_at", "_opt10055_probe_last_request_at", "_close_metrics_last_request_at"))
        return time.monotonic() - last >= self.gap()

    def _due(self, item: dict[str, Any]) -> tuple[bool, float]:
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("strength_status") or "").lower()
        if status in {"pending", "requested", "deferred"}:
            return False, -1.0
        interval = self.intervals().get(item.get("lane"), self.intervals()["top300"])
        age = _age(row)
        local = self.local_last.get(item["stock_code"])
        if local is not None:
            local_age = time.monotonic() - local
            age = local_age if age is None else min(age, local_age)
        if status in {"error", "timeout"}:
            interval = min(interval, 60.0)
        return (True, float("inf")) if age is None else (age >= interval, age / max(interval, 1.0))

    def cycle(self) -> None:
        self.last_cycle_at = datetime.now().isoformat(timespec="seconds")
        self._refresh()
        if not self.payload:
            return
        if not self._idle():
            self.busy_skips += 1
            return
        lane_order = {"s1": 0, "top20": 1, "hidden50": 2, "top300": 3}
        candidates = []
        for index, item in enumerate(build_lane_plan(self.base, self.payload, self.selected)):
            due, overdue = self._due(item)
            if due:
                candidates.append((lane_order[item["lane"]], -overdue, index, item))
        if not candidates:
            return
        item = min(candidates)[3]
        code, lane = item["stock_code"], item["lane"]
        response = self.provider.enqueue_strength_probe(code, priority="active" if lane in {"s1", "top20"} else "background", force=True)
        if str((response or {}).get("status") or "") in {"pending", "requested", "deferred"}:
            self.local_last[code] = time.monotonic()
            self.last_code, self.last_lane = code, lane
            self.enqueue_count += 1

    def run(self) -> None:
        while not self.stop_event.wait(0.25):
            try:
                self.cycle()
            except Exception as error:
                self.last_error = f"cycle: {error}"
                self.stop_event.wait(1.0)

    def stop(self) -> None:
        self.stop_event.set()

    def stats(self) -> dict[str, Any]:
        return {"enabled": True, "alive": self.is_alive(), "opening_burst": self.opening(), "intervals_sec": self.intervals(), "global_gap_sec": self.gap(), "selected_code": self.selected or None, "last_enqueued_code": self.last_code or None, "last_enqueued_lane": self.last_lane or None, "enqueue_count": self.enqueue_count, "skip_busy_count": self.busy_skips, "snapshot_error_count": self.snapshot_errors, "last_cycle_at": self.last_cycle_at, "last_error": self.last_error}


def install(base) -> None:
    provider_class = base.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_strength5m_installed", False):
        return
    original_register = provider_class.register_codes
    original_stop = provider_class.stop
    original_status = provider_class.status

    def enabled() -> bool:
        return str(os.getenv("STOCKBOARD_STRENGTH_5M_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}

    def register(self, codes):
        result = original_register(self, codes)
        scheduler = getattr(self, "_stockboard_strength5m_scheduler", None)
        if result and enabled() and (scheduler is None or not scheduler.is_alive()):
            scheduler = Strength5mScheduler(base, self)
            self._stockboard_strength5m_scheduler = scheduler
            scheduler.start()
        return result

    def stop(self):
        scheduler = getattr(self, "_stockboard_strength5m_scheduler", None)
        if scheduler is not None:
            scheduler.stop()
            if scheduler.is_alive():
                scheduler.join(timeout=2.0)
        return original_stop(self)

    def status(self):
        result = original_status(self)
        result = result if isinstance(result, dict) else {"status": result}
        scheduler = getattr(self, "_stockboard_strength5m_scheduler", None)
        result["strength5m_scheduler"] = scheduler.stats() if scheduler is not None else {"enabled": enabled(), "alive": False}
        return result

    provider_class.register_codes = register
    provider_class.stop = stop
    provider_class.status = status
    provider_class._stockboard_strength5m_installed = True
