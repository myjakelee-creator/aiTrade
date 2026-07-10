from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any

from realtime_v2.market_session import market_session_now
from realtime_v2.strength5m_scheduler import (
    _age as _strength_age,
    _code,
    _read_json_url,
    _refresh_selected,
    _load_selected,
    build_lane_plan,
)


_ORDERBOOK_NORMAL = {"s1": 5.0, "top20": 45.0, "hidden50": 300.0, "top300": 1200.0}
_ORDERBOOK_OPENING = {"s1": 10.0, "top20": 90.0, "hidden50": 600.0, "top300": 1800.0}


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


def _orderbook_age(row: dict[str, Any]) -> float | None:
    parsed = _timestamp(
        row.get("orderbook_completed_at")
        or row.get("orderbook_snapshot_at")
        or row.get("orderbook_received_at")
        or row.get("last_valid_orderbook_at")
    )
    return max(0.0, (datetime.now() - parsed).total_seconds()) if parsed else None


def _has_positive_bidask(row: dict[str, Any]) -> bool:
    for key in ("bid_ask_ratio", "last_valid_bid_ask_ratio", "regular_close_bid_ask_ratio"):
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return True
    return False


def _clock_minutes(text: Any, fallback: str) -> int:
    value = str(text or fallback).strip()
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


class OrderbookThinScheduler(threading.Thread):
    def __init__(self, base, provider) -> None:
        super().__init__(name="StockBoardOrderbookThinScheduler", daemon=True)
        self.base = base
        self.provider = provider
        self.stop_event = threading.Event()
        self.url = os.getenv(
            "STOCKBOARD_V2_WORKER_SNAPSHOT_URL",
            "http://127.0.0.1:8765/api/v2/snapshot?limit=300",
        )
        self.poll_sec = max(2.0, float(os.getenv("STOCKBOARD_BIDASK_SNAPSHOT_POLL_SEC", "5")))
        self.normal_gap = max(1.1, float(os.getenv("STOCKBOARD_BIDASK_GLOBAL_GAP_SEC", "1.5")))
        self.opening_gap = max(self.normal_gap, float(os.getenv("STOCKBOARD_BIDASK_OPENING_GAP_SEC", "2.5")))
        self.selected = _load_selected(base)
        self.payload: dict[str, Any] = {}
        self.last_poll = 0.0
        self.local_last: dict[str, float] = {}
        self.enqueue_count = 0
        self.busy_skips = 0
        self.market_closed_skips = 0
        self.snapshot_errors = 0
        self.last_code = ""
        self.last_lane = ""
        self.last_cycle_at = None
        self.last_error = None
        self.last_market_phase = "unknown"

    def _session(self):
        return market_session_now()

    def opening(self, session=None) -> bool:
        session = session or self._session()
        windows = session.windows or {}
        regular_start = _clock_minutes(windows.get("regular_start"), "09:00")
        now = datetime.now()
        minute = now.hour * 60 + now.minute
        return regular_start - 5 <= minute < regular_start + 10

    def intervals(self, session=None) -> dict[str, float]:
        return _ORDERBOOK_OPENING if self.opening(session) else _ORDERBOOK_NORMAL

    def gap(self, session=None) -> float:
        return self.opening_gap if self.opening(session) else self.normal_gap

    def _market_accepts_query(self, session=None) -> bool:
        session = session or self._session()
        self.last_market_phase = session.phase
        return bool(session.is_trading_day and session.accept_realtime)

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

    def _idle(self, session=None) -> bool:
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
        return time.monotonic() - last >= self.gap(session)

    def _due(self, item: dict[str, Any], session) -> tuple[bool, float]:
        row = item.get("row") if isinstance(item.get("row"), dict) else {}
        status = str(row.get("orderbook_status") or "").lower()
        if status in {"pending", "requested", "deferred"}:
            return False, -1.0
        intervals = self.intervals(session)
        interval = intervals.get(item.get("lane"), intervals["top300"])
        age = _orderbook_age(row)
        local = self.local_last.get(item["stock_code"])
        if local is not None:
            local_age = time.monotonic() - local
            age = local_age if age is None else min(age, local_age)
        if status in {"error", "timeout"}:
            interval = min(interval, 120.0)
        if age is None:
            return True, float("inf")
        # A missing/invalid visible ratio should be refreshed even if the timestamp is recent.
        if not _has_positive_bidask(row):
            return True, max(1.0, age / max(interval, 1.0))
        return age >= interval, age / max(interval, 1.0)

    def cycle(self) -> None:
        self.last_cycle_at = datetime.now().isoformat(timespec="seconds")
        self._refresh()
        if not self.payload:
            return
        session = self._session()
        if not self._market_accepts_query(session):
            self.market_closed_skips += 1
            return
        if not self._idle(session):
            self.busy_skips += 1
            return
        lane_order = {"s1": 0, "top20": 1, "hidden50": 2, "top300": 3}
        candidates = []
        for index, item in enumerate(build_lane_plan(self.base, self.payload, self.selected)):
            due, overdue = self._due(item, session)
            if due:
                candidates.append((lane_order.get(item["lane"], 9), -overdue, index, item))
        if not candidates:
            return
        _lane_priority, _overdue, _index, item = min(candidates)
        code = item["stock_code"]
        lane = item["lane"]
        try:
            response = self.provider.enqueue_orderbook_probe(
                code,
                priority="active" if lane in {"s1", "top20"} else "background",
                force=True,
            )
        except Exception as error:
            self.last_error = f"enqueue_orderbook_probe: {error}"
            return
        status = str((response or {}).get("status") or "")
        if status in {"pending", "requested", "deferred", "cached"}:
            now_mono = time.monotonic()
            self.local_last[code] = now_mono
            self.last_code = code
            self.last_lane = lane
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
        return {
            "enabled": True,
            "alive": self.is_alive(),
            "market_phase": session.phase,
            "market_accepts_query": self._market_accepts_query(session),
            "opening_burst": self.opening(session),
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
    if getattr(provider_class, "_stockboard_orderbook_thin_installed", False):
        return

    original_register = provider_class.register_codes
    original_stop = provider_class.stop
    original_status = provider_class.status

    def enabled() -> bool:
        return str(os.getenv("STOCKBOARD_BIDASK_THIN_ENABLED", "1")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def register(self, codes):
        result = original_register(self, codes)
        scheduler = getattr(self, "_stockboard_orderbook_thin_scheduler", None)
        if result and enabled() and (scheduler is None or not scheduler.is_alive()):
            scheduler = OrderbookThinScheduler(base, self)
            self._stockboard_orderbook_thin_scheduler = scheduler
            scheduler.start()
        return result

    def stop(self):
        scheduler = getattr(self, "_stockboard_orderbook_thin_scheduler", None)
        if scheduler is not None:
            scheduler.stop()
            if scheduler.is_alive():
                scheduler.join(timeout=2.0)
        return original_stop(self)

    def status(self):
        result = original_status(self)
        result = result if isinstance(result, dict) else {"status": result}
        scheduler = getattr(self, "_stockboard_orderbook_thin_scheduler", None)
        result["orderbook_thin_scheduler"] = (
            scheduler.stats()
            if scheduler is not None
            else {"enabled": enabled(), "alive": False}
        )
        return result

    provider_class.register_codes = register
    provider_class.stop = stop
    provider_class.status = status
    provider_class._stockboard_orderbook_thin_installed = True
