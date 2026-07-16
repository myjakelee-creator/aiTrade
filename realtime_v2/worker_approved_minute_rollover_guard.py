from __future__ import annotations

import time
from datetime import datetime

from realtime_v2.market_session import market_session_now

PATCH_VERSION = "approved_minute_rollover_guard_v1"
COMPLETE_START_PHASES = {"premarket", "opening_call"}


def _date_digits(value) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def install(base) -> None:
    """Reset internal minute accumulators exactly at the calendar trading-day rollover.

    A worker that starts after regular trading has already begun cannot prove that the
    earlier large trades were observed, so its quality starts as GAP_POSSIBLE. A worker
    already running at the next premarket rollover clears all intraday accumulators and
    starts the new trading date with complete coverage.
    """

    state_class = getattr(base, "State", None)
    if state_class is None or getattr(
        state_class,
        "_stockboard_approved_minute_rollover_guard_installed",
        False,
    ):
        return

    original_state_init = state_class.__init__
    original_ensure = getattr(state_class, "ensure_metric_session_state_date", None)
    original_stage_trade = getattr(state_class, "stage_approved_trade_events", None)
    original_rows = state_class.rows

    def state_init(self, *args, **kwargs):
        original_state_init(self, *args, **kwargs)
        session = market_session_now(datetime.now())
        target = _date_digits(
            self.status.get("metric_session_state_date")
            or getattr(session, "trading_date", "")
            or getattr(session, "calendar_date", "")
        )
        phase = str(getattr(session, "phase", "") or "")
        restored = int(self.status.get("approved_large_checkpoint_restored_count") or 0)
        self._approved_pipeline_date = target
        self._approved_large_full_session_coverage = (
            phase in COMPLETE_START_PHASES and restored == 0
        )
        with self.lock:
            self.status["approved_minute_rollover_guard_installed"] = True
            self.status["approved_minute_rollover_guard_version"] = PATCH_VERSION
            self.status["approved_pipeline_trading_date"] = target or None
            self.status["approved_large_full_session_coverage"] = bool(
                self._approved_large_full_session_coverage
            )
            if not self._approved_large_full_session_coverage:
                for live in self._approved_large_live.values():
                    if isinstance(live, dict):
                        live["quality"] = "GAP_POSSIBLE"

    def reset_for_date(self, target: str, phase: str) -> None:
        with self.lock:
            self._approved_execution_stage.clear()
            self._approved_orderbook_stage.clear()
            self._approved_strength_stage.clear()
            self._approved_large_live.clear()
            self._approved_large_seen.clear()
            self._approved_trade_value_last.clear()
            self._approved_trade_value_buckets.clear()
            self._approved_trade_value_partial.clear()
            self._approved_last_publish_minute = int(time.time() // 60)
            self._approved_pipeline_date = target
            self._approved_large_full_session_coverage = phase in COMPLETE_START_PHASES
            self.status["approved_pipeline_trading_date"] = target or None
            self.status["approved_pipeline_rollover_at"] = __import__(
                "realtime_v2.common", fromlist=["now_text"]
            ).now_text()
            self.status["approved_pipeline_rollover_count"] = int(
                self.status.get("approved_pipeline_rollover_count") or 0
            ) + 1
            self.status["approved_large_full_session_coverage"] = bool(
                self._approved_large_full_session_coverage
            )

    def ensure_date(self, *args, **kwargs):
        target = original_ensure(self, *args, **kwargs) if callable(original_ensure) else ""
        target = _date_digits(target)
        previous = _date_digits(getattr(self, "_approved_pipeline_date", ""))
        if target and target != previous:
            phase = str(getattr(market_session_now(datetime.now()), "phase", "") or "")
            reset_for_date(self, target, phase)
        return target

    def rows(self, limit: int = 300):
        if callable(original_ensure):
            ensure_date(self)
        return original_rows(self, limit)

    def stage_trade_events(self, events):
        if callable(original_ensure):
            ensure_date(self)
        result = original_stage_trade(self, events) if callable(original_stage_trade) else None
        if not bool(getattr(self, "_approved_large_full_session_coverage", False)):
            with self.lock:
                for live in self._approved_large_live.values():
                    if isinstance(live, dict):
                        live["quality"] = "GAP_POSSIBLE"
                self.status["approved_large_full_session_coverage"] = False
        return result

    state_class.__init__ = state_init
    state_class.reset_approved_minute_pipeline_for_date = reset_for_date
    if callable(original_ensure):
        state_class.ensure_metric_session_state_date = ensure_date
    state_class.rows = rows
    if callable(original_stage_trade):
        state_class.stage_approved_trade_events = stage_trade_events
    state_class._stockboard_approved_minute_rollover_guard_installed = True

    from realtime_v2.worker_momentum_1m_patch import install as install_momentum_1m

    install_momentum_1m(base)
