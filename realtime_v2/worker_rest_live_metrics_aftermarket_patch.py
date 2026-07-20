from __future__ import annotations

from typing import Any


def _clock_minutes(text: Any, fallback: str) -> int:
    value = str(text or fallback).strip()
    try:
        hour_text, minute_text = value.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def install() -> None:
    """Allow the staged REST metric updater to follow NXT aftermarket safely.

    The production price collector remains unchanged. Regular-session REST queries
    keep the integrated ``_AL`` code, while the 15:30-20:00 aftermarket window uses
    the NXT ``_NX`` code. Stocks without an NXT quote keep their same-day last valid
    regular-session value because an empty REST result never overwrites the row.
    """

    import realtime_v2.worker_rest_live_metrics_patch as module

    updater_class = module.RestLiveMetricUpdater
    if getattr(updater_class, "_stockboard_aftermarket_rest_installed", False):
        return

    original_interval = updater_class._interval
    original_status = updater_class._status

    def session_phase(self) -> str:
        minute = self._minute_now()
        regular = self.config.get("regular_session") or {}
        regular_start = _clock_minutes(regular.get("start"), "09:00")
        regular_end = _clock_minutes(regular.get("end"), "15:30")
        if regular_start <= minute < regular_end:
            return "regular"

        aftermarket = self.config.get("aftermarket_session") or {}
        aftermarket_start = _clock_minutes(aftermarket.get("start"), "15:30")
        aftermarket_end = _clock_minutes(aftermarket.get("end"), "20:00")
        if aftermarket_start <= minute < aftermarket_end:
            return "aftermarket"
        return "outside"

    def active_session(self) -> bool:
        return session_phase(self) in {"regular", "aftermarket"}

    def patched_interval(self, metric: str, lane: str) -> float:
        if session_phase(self) == "aftermarket":
            config = self._metric_config(metric)
            values = (
                config.get("aftermarket_interval_sec")
                if isinstance(config.get("aftermarket_interval_sec"), dict)
                else {}
            )
            try:
                return max(0.0, float(values.get(lane) or 0.0))
            except (TypeError, ValueError):
                return 0.0
        return original_interval(self, metric, lane)

    def patched_query_code(self, code: str) -> str:
        phase = session_phase(self)
        mapping = self.config.get("query_suffix_by_session")
        suffix = None
        if isinstance(mapping, dict):
            suffix = mapping.get(phase)
        if suffix in (None, ""):
            suffix = self.config.get("query_suffix") or ""
        suffix = str(suffix).strip()
        query_code = f"{code}{suffix}" if suffix else code
        with self.state.lock:
            self.state.status["rest_live_metrics_market_phase"] = phase
            self.state.status["rest_live_metrics_query_suffix"] = suffix
            self.state.status["rest_live_metrics_query_code"] = query_code
        return query_code

    def patched_status(self, **values):
        values.setdefault("rest_live_metrics_market_phase", session_phase(self))
        return original_status(self, **values)

    updater_class._session_phase = session_phase
    # The original method name is retained for compatibility with the worker loop
    # and price-stall guard; its meaning becomes "active metric session".
    updater_class._in_regular_session = active_session
    updater_class._interval = patched_interval
    updater_class._query_code = patched_query_code
    updater_class._status = patched_status
    updater_class._stockboard_aftermarket_rest_installed = True
