from __future__ import annotations

import time
from typing import Any

PATCH_VERSION = "before_market_integrated_backfill_v1"


def _clock_minutes(value: Any, fallback: str) -> int:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def install() -> None:
    """Permit low-frequency previous-session metric backfill before 08:00.

    Only bid/ask and five-minute strength have positive before-market intervals.
    Large-trade requests remain disabled. The existing single REST worker and global
    request budget are reused, and the collector trade-stall guard is intentionally
    not applied while the market is closed because no trades are expected then.
    """

    import realtime_v2.worker_rest_live_metrics_patch as module

    updater_class = module.RestLiveMetricUpdater
    if getattr(updater_class, "_stockboard_before_market_backfill_installed", False):
        return

    original_session_phase = updater_class._session_phase
    original_interval = updater_class._interval
    original_price_healthy = updater_class._price_healthy
    original_status = updater_class._status

    def session_phase(self) -> str:
        phase = original_session_phase(self)
        if phase != "outside":
            return phase
        config = self.config.get("before_market_session") or {}
        minute = self._minute_now()
        start = _clock_minutes(config.get("start"), "00:00")
        end = _clock_minutes(config.get("end"), "08:00")
        return "before_market" if start <= minute < end else "outside"

    def active_session(self) -> bool:
        return session_phase(self) in {"before_market", "regular", "aftermarket"}

    def interval(self, metric: str, lane: str) -> float:
        if session_phase(self) != "before_market":
            return original_interval(self, metric, lane)
        config = self._metric_config(metric)
        values = (
            config.get("before_market_interval_sec")
            if isinstance(config.get("before_market_interval_sec"), dict)
            else {}
        )
        try:
            return max(0.0, float(values.get(lane) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def query_code(self, code: str) -> str:
        phase = session_phase(self)
        mapping = self.config.get("query_suffix_by_session")
        suffix = mapping.get(phase) if isinstance(mapping, dict) else None
        if suffix in (None, ""):
            suffix = self.config.get("query_suffix") or ""
        suffix = str(suffix).strip()
        query = f"{code}{suffix}" if suffix else code
        with self.state.lock:
            self.state.status["rest_live_metrics_market_phase"] = phase
            self.state.status["rest_live_metrics_query_suffix"] = suffix
            self.state.status["rest_live_metrics_query_code"] = query
        return query

    def price_healthy(self) -> bool:
        if session_phase(self) != "before_market":
            return original_price_healthy(self)
        status = self._collector_status()
        ready = (
            status.get("running") is True
            and str(status.get("login_state") or "") == "connected"
            and status.get("realreg_succeeded") is True
            and int(status.get("realreg_code_count") or 0) > 0
            and not status.get("last_error")
        )
        now_mono = time.monotonic()
        if not ready:
            self.health_stable_since = None
            return False
        if self.health_stable_since is None:
            self.health_stable_since = now_mono
            return False
        stable_sec = float(self.config.get("health_stable_sec") or 10)
        return now_mono - self.health_stable_since >= max(0.0, stable_sec)

    def status(self, **values):
        values.setdefault("rest_live_metrics_market_phase", session_phase(self))
        values.setdefault("rest_live_metrics_before_market_patch", PATCH_VERSION)
        return original_status(self, **values)

    updater_class._session_phase = session_phase
    updater_class._in_regular_session = active_session
    updater_class._interval = interval
    updater_class._query_code = query_code
    updater_class._price_healthy = price_healthy
    updater_class._status = status
    updater_class._stockboard_before_market_backfill_installed = True
