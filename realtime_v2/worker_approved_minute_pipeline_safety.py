from __future__ import annotations

import time
from copy import deepcopy
from typing import Any

from realtime_v2.common import normalize_code, now_text

PATCH_VERSION = "approved_minute_pipeline_safety_v1"
CHECKPOINT_INTERVAL_SEC = 5.0
CHECKPOINT_KEYS = (
    "approved_large_checkpoint_buy_count",
    "approved_large_checkpoint_sell_count",
    "approved_large_checkpoint_buy_sum_eok",
    "approved_large_checkpoint_sell_sum_eok",
    "approved_large_checkpoint_quality",
    "approved_large_checkpoint_observed_at",
    "approved_large_checkpoint_source_date",
)


def install(base) -> None:
    """Add crash checkpoints and fail-closed orderbook rotation.

    The live large-trade counters are copied to daily state at most once every five
    seconds; no event is written directly to disk. A restart restores the checkpoint
    with GAP_POSSIBLE quality because events during process downtime cannot be proven.

    If the rotating orderbook registration produces no orderbook event for the configured
    timeout, only that orderbook group is disabled. The existing Top100 trade stream and
    verified 32-bit price collector continue unchanged, and REST orderbook fallback is not
    enabled.
    """

    import realtime_v2.worker_realtime_strength_ws_patch as ws_module

    state_class = getattr(base, "State", None)
    updater_class = ws_module.RealtimeStrengthWebSocket
    if state_class is None or getattr(
        state_class,
        "_stockboard_approved_minute_pipeline_safety_installed",
        False,
    ):
        return

    base.DAILY_PERSIST_KEYS = tuple(
        dict.fromkeys((*getattr(base, "DAILY_PERSIST_KEYS", ()), *CHECKPOINT_KEYS))
    )

    original_state_init = state_class.__init__
    original_stage_trade = getattr(state_class, "stage_approved_trade_events", None)
    original_mark_disconnect = getattr(state_class, "mark_approved_stream_disconnect", None)
    original_status = updater_class._status

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        self._approved_large_checkpoint_last_mono = 0.0
        restored = 0
        with self.lock:
            for code, daily in self.daily_values_by_code.items():
                if not isinstance(daily, dict):
                    continue
                if "approved_large_checkpoint_buy_count" not in daily:
                    continue
                normalized = normalize_code(code)
                if not normalized:
                    continue
                self._approved_large_live[normalized] = {
                    "buy_count": int(daily.get("approved_large_checkpoint_buy_count") or 0),
                    "sell_count": int(daily.get("approved_large_checkpoint_sell_count") or 0),
                    "buy_sum_eok": float(daily.get("approved_large_checkpoint_buy_sum_eok") or 0.0),
                    "sell_sum_eok": float(daily.get("approved_large_checkpoint_sell_sum_eok") or 0.0),
                    "quality": "GAP_POSSIBLE",
                    "observed_at": daily.get("approved_large_checkpoint_observed_at"),
                    "source_date": daily.get("approved_large_checkpoint_source_date"),
                }
                restored += 1
            self.status["approved_minute_pipeline_safety_installed"] = True
            self.status["approved_minute_pipeline_safety_version"] = PATCH_VERSION
            self.status["approved_large_checkpoint_interval_sec"] = CHECKPOINT_INTERVAL_SEC
            self.status["approved_large_checkpoint_restored_count"] = restored

    def checkpoint_large(self, *, force: bool = False) -> bool:
        now_mono = time.monotonic()
        last = float(getattr(self, "_approved_large_checkpoint_last_mono", 0.0) or 0.0)
        if not force and now_mono - last < CHECKPOINT_INTERVAL_SEC:
            return False
        checkpointed = 0
        with self.lock:
            for code, live in self._approved_large_live.items():
                if not isinstance(live, dict):
                    continue
                daily = self.daily_values_by_code.setdefault(code, {})
                values = {
                    "approved_large_checkpoint_buy_count": int(live.get("buy_count") or 0),
                    "approved_large_checkpoint_sell_count": int(live.get("sell_count") or 0),
                    "approved_large_checkpoint_buy_sum_eok": round(
                        float(live.get("buy_sum_eok") or 0.0), 4
                    ),
                    "approved_large_checkpoint_sell_sum_eok": round(
                        float(live.get("sell_sum_eok") or 0.0), 4
                    ),
                    "approved_large_checkpoint_quality": str(
                        live.get("quality") or "EXACT_LIVE"
                    ),
                    "approved_large_checkpoint_observed_at": live.get("observed_at")
                    or now_text(),
                    "approved_large_checkpoint_source_date": live.get("source_date"),
                }
                for key, value in values.items():
                    if value not in (None, ""):
                        daily[key] = deepcopy(value)
                checkpointed += 1
            self._approved_large_checkpoint_last_mono = now_mono
            self.status["approved_large_checkpoint_count"] = checkpointed
            self.status["approved_large_checkpoint_last_at"] = now_text()
            if checkpointed:
                self._mark_daily_dirty()
        return checkpointed > 0

    def stage_trade_events(self, events):
        result = original_stage_trade(self, events) if callable(original_stage_trade) else None
        checkpoint_large(self)
        return result

    def mark_disconnect(self):
        result = original_mark_disconnect(self) if callable(original_mark_disconnect) else None
        checkpoint_large(self, force=True)
        return result

    def updater_status(self, **values: Any) -> None:
        original_status(self, **values)
        now_mono = time.monotonic()
        if "approved_stream_connected_at" in values:
            self._approved_orderbook_connection_started_mono = now_mono
            with self.state.lock:
                self._approved_orderbook_event_baseline = int(
                    self.state.status.get("approved_orderbook_raw_event_count") or 0
                )
            return

        orderbook_config = self.config.get("realtime_orderbook_ws")
        orderbook_config = orderbook_config if isinstance(orderbook_config, dict) else {}
        if not bool(orderbook_config.get("enabled", True)):
            return
        started = float(
            getattr(self, "_approved_orderbook_connection_started_mono", 0.0) or 0.0
        )
        if started <= 0:
            return
        timeout = max(30.0, float(orderbook_config.get("no_event_disable_after_sec") or 180))
        if now_mono - started < timeout:
            return
        baseline = int(getattr(self, "_approved_orderbook_event_baseline", 0) or 0)
        with self.state.lock:
            current = int(self.state.status.get("approved_orderbook_raw_event_count") or 0)
        if current > baseline:
            return

        mutable = self.config.setdefault("realtime_orderbook_ws", {})
        if isinstance(mutable, dict):
            mutable["enabled"] = False
        with self.state.lock:
            self.state.status["approved_orderbook_ws_enabled"] = False
            self.state.status["approved_orderbook_ws_status"] = "disabled_no_events"
            self.state.status["approved_orderbook_ws_disabled_at"] = now_text()
            self.state.status["approved_orderbook_ws_fallback"] = "dash_no_rest_fallback"
        connection = None
        with self.connection_lock:
            connection = self.connection
        if connection is not None:
            connection.close()

    state_class.__init__ = state_init
    state_class.checkpoint_approved_large_trade = checkpoint_large
    if callable(original_stage_trade):
        state_class.stage_approved_trade_events = stage_trade_events
    if callable(original_mark_disconnect):
        state_class.mark_approved_stream_disconnect = mark_disconnect
    updater_class._status = updater_status
    state_class._stockboard_approved_minute_pipeline_safety_installed = True
