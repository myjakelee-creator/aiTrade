from __future__ import annotations

from datetime import datetime
from typing import Any

from realtime_v2.common import normalize_code
from realtime_v2.market_session import market_session_now

PATCH_VERSION = "approved_minute_runtime_fix_v1"


def _clock_minutes(value: Any, fallback: str) -> int:
    text = str(value or fallback).strip()
    try:
        hour_text, minute_text = text.split(":", 1)
        return int(hour_text) * 60 + int(minute_text[:2])
    except (TypeError, ValueError):
        hour_text, minute_text = fallback.split(":", 1)
        return int(hour_text) * 60 + int(minute_text)


def install(base) -> None:
    import realtime_v2.worker_rest_live_metrics_patch as rest_module

    state_class = getattr(base, "State", None)
    updater_class = rest_module.RestLiveMetricUpdater
    if state_class is None or getattr(
        state_class,
        "_stockboard_approved_minute_runtime_fix_installed",
        False,
    ):
        return

    original_stage_trade = getattr(state_class, "stage_approved_trade_events", None)
    original_publish = getattr(state_class, "publish_approved_minute_metrics", None)
    original_next_task = getattr(updater_class, "_next_task", None)

    def stage_trade_events(self, events):
        if callable(original_stage_trade):
            original_stage_trade(self, events)
        observed_at = __import__("realtime_v2.common", fromlist=["now_text"]).now_text()
        source_date = __import__(
            "realtime_v2.worker_approved_minute_pipeline",
            fromlist=["_source_date"],
        )._source_date()
        with self.lock:
            for event in events or ():
                if not isinstance(event, dict):
                    continue
                code = normalize_code(event.get("stock_code"))
                qty = event.get("signed_trade_qty")
                if not code or qty in (None, "", 0, "0"):
                    continue
                live = self._approved_large_live.setdefault(
                    code,
                    {
                        "buy_count": 0,
                        "sell_count": 0,
                        "buy_sum_eok": 0.0,
                        "sell_sum_eok": 0.0,
                        "quality": "EXACT_LIVE",
                    },
                )
                live.setdefault("quality", "EXACT_LIVE")
                live["observed_at"] = live.get("observed_at") or observed_at
                live["source_date"] = live.get("source_date") or source_date

    def publish(self, force: bool = False):
        strength_count = len(getattr(self, "_approved_strength_stage", {}) or {})
        result = original_publish(self, force) if callable(original_publish) else False
        if result:
            with self.lock:
                self.status["approved_strength_published_count"] = strength_count
        return result

    def next_task(self):
        if not callable(original_next_task):
            return None
        config = getattr(self, "config", {}) or {}
        phase_method = getattr(self, "_session_phase", None)
        phase = phase_method() if callable(phase_method) else ""
        if phase == "opening_burst":
            now = datetime.now()
            session = market_session_now(now)
            windows = session.windows if isinstance(session.windows, dict) else {}
            regular_start = _clock_minutes(windows.get("regular_start"), "09:00")
            elapsed = now.hour * 60 + now.minute - regular_start
            pipeline = config.get("approved_minute_pipeline")
            pipeline = pipeline if isinstance(pipeline, dict) else {}
            delay = max(0, int(pipeline.get("strength_open_delay_minutes") or 5))
            if elapsed < delay:
                with self.state.lock:
                    self.state.status["approved_strength_open_delay_active"] = True
                    self.state.status["approved_strength_open_delay_minutes"] = delay
                return None
        with self.state.lock:
            self.state.status["approved_strength_open_delay_active"] = False
        return original_next_task(self)

    if callable(original_stage_trade):
        state_class.stage_approved_trade_events = stage_trade_events
    if callable(original_publish):
        state_class.publish_approved_minute_metrics = publish
    if callable(original_next_task):
        updater_class._next_task = next_task
    state_class._stockboard_approved_minute_runtime_fix_installed = True
