from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from realtime_v2.market_session import (
    last_completed_trading_date,
    market_session_now,
    next_premarket_datetime,
)

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
        temp.write_text(
            json.dumps(
                {
                    "stock_code": code,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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
    """Classify query priority using the already-stable worker display order."""

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
        (
            (index, row)
            for index, row in enumerate(rows[20:], 21)
            if 21 <= _model_rank(row, index) <= 50
        ),
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
    parsed = _timestamp(
        row.get("strength_completed_at")
        or row.get("strength_snapshot_at")
        or row.get("last_valid_strength_at")
    )
    return max(0.0, (datetime.now() - parsed).total_seconds()) if parsed else None


def _positive_strength(row: dict[str, Any]) -> float | None:
    try:
        value = float(row.get("strength_5m"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _clock_minutes(text: Any, fallback: str) -> int:
    value = str(text or fallback).strip()
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def _clock_datetime(now: datetime, text: Any, fallback: str) -> datetime:
    minutes = _clock_minutes(text, fallback)
    return now.replace(
        hour=minutes // 60,
        minute=minutes % 60,
        second=0,
        microsecond=0,
    )


class Strength5mScheduler(threading.Thread):
    NORMAL = {"s1": 30.0, "top20": 90.0, "hidden50": 300.0, "top300": 1200.0}
    OPENING = {"s1": 45.0, "top20": 120.0, "hidden50": 600.0, "top300": 1800.0}
    PREOPEN_PHASES = {"after_wait", "aftermarket", "closed", "before_market", "weekend", "holiday"}
    PREOPEN_TERMINAL_ERROR = {"error", "timeout", "failed", "unavailable", "not_connected"}
    PREOPEN_EMPTY_STATUS = {"held_last_valid", "missing", "no_data", "empty", "blank"}
    PREOPEN_RETRY_DELAYS = (300.0, 1800.0, 7200.0)

    def __init__(self, base, provider) -> None:
        super().__init__(name="StockBoardStrength5mScheduler", daemon=True)
        self.base, self.provider = base, provider
        self.stop_event = threading.Event()
        self.url = os.getenv(
            "STOCKBOARD_V2_WORKER_SNAPSHOT_URL",
            "http://127.0.0.1:8765/api/v2/snapshot?limit=300",
        )
        self.poll_sec = max(
            2.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_SNAPSHOT_POLL_SEC", "5")),
        )
        self.normal_gap = max(
            1.05,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_GLOBAL_GAP_SEC", "1.25")),
        )
        self.opening_gap = max(
            self.normal_gap,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_OPENING_GAP_SEC", "2")),
        )
        self.preopen_after_close_gap = max(
            self.normal_gap,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_AFTER_CLOSE_GAP_SEC", "2")),
        )
        self.preopen_offday_gap = max(
            self.preopen_after_close_gap,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_OFFDAY_GAP_SEC", "10")),
        )
        self.preopen_final_gap = max(
            self.normal_gap,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_FINAL_GAP_SEC", "3")),
        )
        self.preopen_final_window_sec = max(
            300.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_FINAL_WINDOW_SEC", "1800")),
        )
        self.preopen_global_error_limit = max(
            1,
            int(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_GLOBAL_ERROR_LIMIT", "3")),
        )
        self.preopen_global_backoff_sec = max(
            60.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_PREOPEN_GLOBAL_BACKOFF_SEC", "1800")),
        )
        self.close_sweep_minutes = max(
            10,
            int(os.getenv("STOCKBOARD_STRENGTH_5M_CLOSE_SWEEP_MINUTES", "270")),
        )
        self.close_sweep_retry_sec = max(
            30.0,
            float(os.getenv("STOCKBOARD_STRENGTH_5M_CLOSE_SWEEP_RETRY_SEC", "60")),
        )
        self.close_sweep_max_attempts = max(
            1,
            int(os.getenv("STOCKBOARD_STRENGTH_5M_CLOSE_SWEEP_MAX_ATTEMPTS", "2")),
        )
        self.selected = _load_selected(base)
        self.payload: dict[str, Any] = {}
        self.last_poll = 0.0
        self.local_last: dict[str, float] = {}
        self.close_sweep_requested_at: dict[str, float] = {}
        self.close_sweep_attempts: dict[str, int] = {}
        self.preopen_attempts: dict[str, int] = {}
        self.preopen_retry_at: dict[str, float] = {}
        self.preopen_requested_at: dict[str, float] = {}
        self.preopen_observed_tokens: dict[str, tuple[Any, ...]] = {}
        self.preopen_final_attempted: set[str] = set()
        self.preopen_final_key = ""
        self.preopen_global_backoff_until = 0.0
        self.preopen_global_backoff_until_iso: str | None = None
        self.preopen_consecutive_errors = 0
        self.preopen_success_count = 0
        self.preopen_empty_count = 0
        self.preopen_error_count = 0
        self.preopen_missing_count = 0
        self.preopen_filled_count = 0
        self.preopen_total_count = 0
        self.preopen_mode = "normal_session"
        self.preopen_current_pass = 0
        self.next_premarket_at: str | None = None
        self.enqueue_count = self.busy_skips = self.snapshot_errors = 0
        self.market_closed_skips = 0
        self.close_sweep_enqueue_count = 0
        self.last_code = self.last_lane = ""
        self.last_cycle_at = self.last_error = None
        self.last_market_phase = "unknown"

    def _session(self):
        return market_session_now()

    def _market_accepts_strength_query(self, session=None) -> bool:
        session = session or self._session()
        self.last_market_phase = session.phase
        return bool(session.is_trading_day and session.accept_realtime)

    def _preopen_window(self, session=None) -> bool:
        session = session or self._session()
        return str(session.phase or "").lower() in self.PREOPEN_PHASES

    def _next_premarket(self, now: datetime | None = None) -> datetime:
        return next_premarket_datetime(now or datetime.now())

    def opening(self, session=None) -> bool:
        session = session or self._session()
        windows = session.windows or {}
        regular_start = _clock_minutes(windows.get("regular_start"), "09:00")
        now = datetime.now()
        minute = now.hour * 60 + now.minute
        return regular_start - 5 <= minute < regular_start + 10

    def intervals(self, session=None) -> dict[str, float]:
        return self.OPENING if self.opening(session) else self.NORMAL

    def gap(self, session=None) -> float:
        return self.opening_gap if self.opening(session) else self.normal_gap

    def _preopen_gap(self, session, next_premarket: datetime, now: datetime | None = None) -> float:
        now = now or datetime.now()
        until_open = max(0.0, (next_premarket - now).total_seconds())
        if until_open <= self.preopen_final_window_sec:
            return self.preopen_final_gap
        if str(session.phase or "").lower() in {"after_wait", "aftermarket"}:
            return self.preopen_after_close_gap
        return self.preopen_offday_gap

    def _close_sweep_cutoff(self, session, now: datetime | None = None) -> datetime | None:
        if not session or not session.is_trading_day:
            return None
        if str(session.phase or "") not in {"after_wait", "aftermarket"}:
            return None
        now = now or datetime.now()
        windows = session.windows or {}
        regular_close = _clock_datetime(now, windows.get("regular_close"), "15:30")
        configured_end = regular_close + timedelta(minutes=self.close_sweep_minutes)
        calendar_end = _clock_datetime(
            now,
            windows.get("aftermarket_end"),
            "20:00",
        )
        sweep_end = min(configured_end, calendar_end)
        return regular_close if regular_close <= now < sweep_end else None

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

    def _idle(self, session=None, gap_override: float | None = None) -> bool:
        provider = self.provider
        lock = getattr(provider, "_lock", None)
        if lock is None:
            return False
        with lock:
            if any(
                (
                    getattr(provider, "_strength_probe_inflight", None),
                    getattr(provider, "_orderbook_probe_inflight", None),
                    getattr(provider, "_opt10055_probe_inflight", None),
                )
            ):
                return False
            queues = (
                "_strength_probe_pending",
                "_orderbook_probe_pending",
                "_opt10055_probe_pending",
                "_close_metrics_queue",
            )
            if any(len(getattr(provider, name, ())) for name in queues):
                return False
            last = max(
                float(getattr(provider, name, 0.0) or 0.0)
                for name in (
                    "_strength_probe_last_request_at",
                    "_orderbook_probe_last_request_at",
                    "_opt10055_probe_last_request_at",
                    "_close_metrics_last_request_at",
                )
            )
        required_gap = self.gap(session) if gap_override is None else max(self.normal_gap, float(gap_override))
        return time.monotonic() - last >= required_gap

    def _close_sweep_due(
        self,
        item: dict[str, Any],
        session,
        now: datetime | None = None,
    ) -> tuple[bool, float]:
        cutoff = self._close_sweep_cutoff(session, now)
        if cutoff is None:
            return False, -1.0
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("strength_status") or "").lower()
        if status in {"pending", "requested", "deferred"}:
            return False, -1.0
        snapshot_at = _timestamp(
            row.get("strength_completed_at")
            or row.get("strength_snapshot_at")
            or row.get("last_valid_strength_at")
        )
        code = item["stock_code"]
        strength_value = _positive_strength(row) or 0.0
        incomplete_statuses = {
            "held_last_valid",
            "error",
            "timeout",
            "missing",
            "no_data",
            "empty",
        }
        if (
            strength_value > 0
            and snapshot_at is not None
            and snapshot_at >= cutoff
            and status not in incomplete_statuses
        ):
            self.close_sweep_requested_at.pop(code, None)
            self.close_sweep_attempts.pop(code, None)
            return False, -1.0
        if self.close_sweep_attempts.get(code, 0) >= self.close_sweep_max_attempts:
            return False, -1.0
        last_request = self.close_sweep_requested_at.get(code)
        if last_request is not None:
            elapsed = time.monotonic() - last_request
            if elapsed < self.close_sweep_retry_sec:
                return False, -1.0
        overdue = (
            float("inf")
            if snapshot_at is None
            else max(0.0, (cutoff - snapshot_at).total_seconds())
        )
        return True, overdue

    def _normal_due(self, item: dict[str, Any], session) -> tuple[bool, float]:
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("strength_status") or "").lower()
        if status in {"pending", "requested", "deferred"}:
            return False, -1.0
        intervals = self.intervals(session)
        interval = intervals.get(item.get("lane"), intervals["top300"])
        age = _age(row)
        local = self.local_last.get(item["stock_code"])
        if local is not None:
            local_age = time.monotonic() - local
            age = local_age if age is None else min(age, local_age)
        if status in {"error", "timeout"}:
            interval = min(interval, 60.0)
        return (
            (True, float("inf"))
            if age is None
            else (age >= interval, age / max(interval, 1.0))
        )

    def _preopen_missing_items(self, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            item
            for item in plan
            if _positive_strength(item.get("row") if isinstance(item.get("row"), dict) else {}) is None
        ]

    def _retry_delay(self, attempt: int) -> float:
        index = max(0, int(attempt) - 1)
        if index >= len(self.PREOPEN_RETRY_DELAYS):
            return self.PREOPEN_RETRY_DELAYS[-1]
        return self.PREOPEN_RETRY_DELAYS[index]

    def _reset_preopen_runtime(self) -> None:
        self.preopen_attempts.clear()
        self.preopen_retry_at.clear()
        self.preopen_requested_at.clear()
        self.preopen_observed_tokens.clear()
        self.preopen_final_attempted.clear()
        self.preopen_final_key = ""
        self.preopen_global_backoff_until = 0.0
        self.preopen_global_backoff_until_iso = None
        self.preopen_consecutive_errors = 0
        self.preopen_missing_count = 0
        self.preopen_filled_count = 0
        self.preopen_total_count = 0
        self.preopen_current_pass = 0
        self.next_premarket_at = None
        self.preopen_mode = "normal_session"

    def _enter_global_backoff(self) -> None:
        self.preopen_global_backoff_until = time.monotonic() + self.preopen_global_backoff_sec
        self.preopen_global_backoff_until_iso = (
            datetime.now() + timedelta(seconds=self.preopen_global_backoff_sec)
        ).isoformat(timespec="seconds")
        self.preopen_consecutive_errors = 0

    def _observe_preopen_results(self, plan: list[dict[str, Any]]) -> None:
        now_mono = time.monotonic()
        by_code = {item["stock_code"]: item for item in plan}
        for code, requested_at in list(self.preopen_requested_at.items()):
            item = by_code.get(code)
            if not item:
                continue
            row = item.get("row") if isinstance(item.get("row"), dict) else {}
            if _positive_strength(row) is not None:
                self.preopen_success_count += 1
                self.preopen_attempts.pop(code, None)
                self.preopen_retry_at.pop(code, None)
                self.preopen_requested_at.pop(code, None)
                self.preopen_observed_tokens.pop(code, None)
                self.preopen_consecutive_errors = 0
                continue
            if now_mono - requested_at < max(5.0, self.poll_sec):
                continue
            status = str(row.get("strength_status") or "").strip().lower()
            token = (
                self.preopen_attempts.get(code, 0),
                status,
                row.get("strength_snapshot_at"),
                row.get("strength_completed_at"),
            )
            if self.preopen_observed_tokens.get(code) == token:
                continue
            self.preopen_observed_tokens[code] = token
            if status in self.PREOPEN_TERMINAL_ERROR:
                self.preopen_error_count += 1
                self.preopen_consecutive_errors += 1
                if self.preopen_consecutive_errors >= self.preopen_global_error_limit:
                    self._enter_global_backoff()
            elif status in self.PREOPEN_EMPTY_STATUS or not status:
                self.preopen_empty_count += 1

    def _prepare_final_pass(self, next_premarket: datetime, now: datetime) -> bool:
        key = next_premarket.isoformat(timespec="minutes")
        if key != self.preopen_final_key:
            self.preopen_final_key = key
            self.preopen_final_attempted.clear()
        return (next_premarket - now).total_seconds() <= self.preopen_final_window_sec

    def _preopen_candidate_due(
        self,
        item: dict[str, Any],
        *,
        final_window: bool,
        now_mono: float | None = None,
    ) -> tuple[bool, float]:
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("strength_status") or "").strip().lower()
        if status in {"pending", "requested", "deferred"}:
            return False, -1.0
        code = item["stock_code"]
        now_mono = time.monotonic() if now_mono is None else now_mono
        if final_window and code not in self.preopen_final_attempted:
            return True, float("inf")
        due_at = float(self.preopen_retry_at.get(code, 0.0) or 0.0)
        if now_mono < due_at:
            return False, due_at - now_mono
        attempt = int(self.preopen_attempts.get(code, 0) or 0)
        return True, float("inf") if attempt == 0 else float(attempt)

    def _mark_preopen_request(self, code: str, *, final_window: bool) -> None:
        now_mono = time.monotonic()
        attempt = int(self.preopen_attempts.get(code, 0) or 0) + 1
        self.preopen_attempts[code] = attempt
        self.preopen_requested_at[code] = now_mono
        self.preopen_retry_at[code] = now_mono + self._retry_delay(attempt)
        self.preopen_observed_tokens.pop(code, None)
        if final_window:
            self.preopen_final_attempted.add(code)
        self.preopen_current_pass = max(
            self.preopen_current_pass,
            max(self.preopen_attempts.values(), default=0),
        )

    def _preopen_cycle(self, session, plan: list[dict[str, Any]]) -> None:
        now = datetime.now()
        next_premarket = self._next_premarket(now)
        self.next_premarket_at = next_premarket.isoformat(timespec="seconds")
        self._observe_preopen_results(plan)

        missing = self._preopen_missing_items(plan)
        self.preopen_total_count = len(plan)
        self.preopen_missing_count = len(missing)
        self.preopen_filled_count = max(0, len(plan) - len(missing))
        if not missing:
            self.preopen_mode = "preopen_fill_complete"
            self.preopen_global_backoff_until = 0.0
            self.preopen_global_backoff_until_iso = None
            self.preopen_consecutive_errors = 0
            return

        now_mono = time.monotonic()
        if now_mono < self.preopen_global_backoff_until:
            self.preopen_mode = "preopen_fill_backoff"
            return

        final_window = self._prepare_final_pass(next_premarket, now)
        gap = self._preopen_gap(session, next_premarket, now)
        if not self._idle(session, gap_override=gap):
            self.preopen_mode = "preopen_fill_active"
            self.busy_skips += 1
            return

        lane_order = {"s1": 0, "top20": 1, "hidden50": 2, "top300": 3}
        candidates = []
        for index, item in enumerate(missing):
            due, score = self._preopen_candidate_due(
                item,
                final_window=final_window,
                now_mono=now_mono,
            )
            if due:
                candidates.append((lane_order[item["lane"]], -score, index, item))
        if not candidates:
            self.preopen_mode = "preopen_fill_active"
            return

        _lane_priority, _score, _index, item = min(candidates)
        code, lane = item["stock_code"], item["lane"]
        source_trading_date = last_completed_trading_date(now) or None
        response = self.provider.enqueue_strength_probe(
            code,
            priority="active" if lane in {"s1", "top20"} else "background",
            force=True,
            trading_date=source_trading_date,
        )
        status = str((response or {}).get("status") or "").strip().lower()
        if status in {"pending", "requested"}:
            self._mark_preopen_request(code, final_window=final_window)
            self.local_last[code] = time.monotonic()
            self.last_code, self.last_lane = code, lane
            self.enqueue_count += 1
            self.preopen_mode = "preopen_fill_active"
            return
        if status == "deferred":
            self.preopen_mode = "preopen_fill_active"
            return
        if status in self.PREOPEN_TERMINAL_ERROR:
            self.preopen_error_count += 1
            self.preopen_consecutive_errors += 1
            if self.preopen_consecutive_errors >= self.preopen_global_error_limit:
                self._enter_global_backoff()
                self.preopen_mode = "preopen_fill_backoff"
            else:
                self.preopen_mode = "preopen_fill_active"
            return
        self.preopen_empty_count += 1
        self.preopen_mode = "preopen_fill_active"

    def cycle(self) -> None:
        self.last_cycle_at = datetime.now().isoformat(timespec="seconds")
        self._refresh()
        if not self.payload:
            return

        session = self._session()
        plan = build_lane_plan(self.base, self.payload, self.selected)
        if self._preopen_window(session):
            self._preopen_cycle(session, plan)
            return

        if self.preopen_mode != "normal_session" or self.preopen_attempts:
            self._reset_preopen_runtime()

        if not self._market_accepts_strength_query(session):
            self.market_closed_skips += 1
            return
        if not self._idle(session):
            self.busy_skips += 1
            return

        lane_order = {"s1": 0, "top20": 1, "hidden50": 2, "top300": 3}
        candidates = []
        for index, item in enumerate(plan):
            due, overdue = self._normal_due(item, session)
            if due:
                candidates.append((lane_order[item["lane"]], -overdue, index, item))
        if not candidates:
            return

        _lane_priority, _overdue, _index, item = min(candidates)
        code, lane = item["stock_code"], item["lane"]
        response = self.provider.enqueue_strength_probe(
            code,
            priority="active" if lane in {"s1", "top20"} else "background",
            force=True,
        )
        if str((response or {}).get("status") or "") in {
            "pending",
            "requested",
            "deferred",
        }:
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
        session = self._session()
        plan = build_lane_plan(self.base, self.payload, self.selected) if self.payload else []
        if self._preopen_window(session):
            missing_count = len(self._preopen_missing_items(plan))
            total_count = len(plan)
            filled_count = max(0, total_count - missing_count)
        else:
            missing_count = self.preopen_missing_count
            total_count = self.preopen_total_count
            filled_count = self.preopen_filled_count
        return {
            "enabled": True,
            "alive": self.is_alive(),
            "mode": self.preopen_mode,
            "market_phase": session.phase,
            "market_accepts_query": self._market_accepts_strength_query(session),
            "query_allowed": self._market_accepts_strength_query(session)
            or self._preopen_window(session),
            "opening_burst": self.opening(session),
            "regular_close_sweep_active": self._close_sweep_cutoff(session) is not None,
            "regular_close_sweep_minutes": self.close_sweep_minutes,
            "regular_close_sweep_enqueue_count": self.close_sweep_enqueue_count,
            "regular_close_sweep_max_attempts": self.close_sweep_max_attempts,
            "preopen_fill_active": self.preopen_mode == "preopen_fill_active",
            "preopen_fill_complete": self.preopen_mode == "preopen_fill_complete",
            "preopen_fill_backoff": self.preopen_mode == "preopen_fill_backoff",
            "next_premarket_at": self.next_premarket_at,
            "missing_count": missing_count,
            "filled_count": filled_count,
            "total_count": total_count,
            "current_pass": self.preopen_current_pass,
            "preopen_success_count": self.preopen_success_count,
            "preopen_empty_count": self.preopen_empty_count,
            "preopen_error_count": self.preopen_error_count,
            "global_backoff_until": self.preopen_global_backoff_until_iso,
            "intervals_sec": self.intervals(session),
            "global_gap_sec": self.gap(session),
            "selected_code": self.selected or None,
            "last_enqueued_code": self.last_code or None,
            "last_enqueued_lane": self.last_lane or None,
            "enqueue_count": self.enqueue_count,
            "skip_busy_count": self.busy_skips,
            "skip_market_closed_count": self.market_closed_skips,
            "snapshot_error_count": self.snapshot_errors,
            "last_cycle_at": self.last_cycle_at,
            "last_error": self.last_error,
        }


def install(base) -> None:
    provider_class = base.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_strength5m_installed", False):
        return
    original_register = provider_class.register_codes
    original_stop = provider_class.stop
    original_status = provider_class.status

    def enabled() -> bool:
        return str(os.getenv("STOCKBOARD_STRENGTH_5M_ENABLED", "1")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

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
        result["strength5m_scheduler"] = (
            scheduler.stats()
            if scheduler is not None
            else {"enabled": enabled(), "alive": False}
        )
        return result

    provider_class.register_codes = register
    provider_class.stop = stop
    provider_class.status = status
    provider_class._stockboard_strength5m_installed = True
