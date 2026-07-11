from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Callable


_ACTIVE_MODES = {
    "rate_gap",
    "rate_gap_recovered",
    "strength_inflight",
    "orderbook_inflight",
    "tick_error_recovered",
    "timer_driver_error_recovered",
}
_IDLE_MODES = {"complete", "inactive_session", "retry_wait", "stopped"}


def _ensure_fields(provider) -> None:
    defaults = {
        "_offhours_metric_timer": None,
        "_offhours_metric_timer_active": False,
        "_offhours_metric_timer_interval_ms": 250,
        "_offhours_metric_timer_active_interval_ms": 250,
        "_offhours_metric_timer_wait_interval_ms": 1000,
        "_offhours_metric_timer_idle_interval_ms": 10000,
        "_offhours_metric_timer_tick_count": 0,
        "_offhours_metric_timer_last_tick_monotonic": 0.0,
        "_offhours_metric_timer_last_tick_at": None,
        "_offhours_metric_timer_error_count": 0,
        "_offhours_metric_timer_last_error": None,
        "_offhours_metric_timer_last_source": None,
        "_offhours_metric_timer_fallback_count": 0,
        "_offhours_metric_timer_drain_run_count": 0,
        "_offhours_metric_timer_last_drain_monotonic": 0.0,
        "_offhours_metric_timer_last_mode": None,
        "_offhours_metric_timer_interval_switch_count": 0,
    }
    for name, value in defaults.items():
        if not hasattr(provider, name):
            setattr(provider, name, value)


def _mode_interval_ms(provider, drain) -> int:
    _ensure_fields(provider)
    mode = str(getattr(drain, "mode", "") or "").strip().lower()
    active_ms = int(provider._offhours_metric_timer_active_interval_ms)
    wait_ms = int(provider._offhours_metric_timer_wait_interval_ms)
    idle_ms = int(provider._offhours_metric_timer_idle_interval_ms)

    if mode in _IDLE_MODES:
        return idle_ms
    if mode in _ACTIVE_MODES or mode.endswith("_inflight"):
        return active_ms
    if mode.startswith("waiting_") or mode in {
        "snapshot_error",
        "timer_driver_error_recovered",
    }:
        return wait_ms
    # Unknown/new modes stay responsive until they settle into a known state.
    return active_ms


def _set_timer_interval(provider, interval_ms: int) -> None:
    _ensure_fields(provider)
    interval_ms = max(100, int(interval_ms))
    current = int(
        getattr(provider, "_offhours_metric_timer_interval_ms", interval_ms)
        or interval_ms
    )
    if current == interval_ms:
        return

    timer = getattr(provider, "_offhours_metric_timer", None)
    try:
        if timer is not None:
            timer.setInterval(interval_ms)
    except Exception as error:
        provider._offhours_metric_timer_error_count += 1
        provider._offhours_metric_timer_last_error = (
            f"timer_interval:{type(error).__name__}: {error}"
        )
        return

    provider._offhours_metric_timer_interval_ms = interval_ms
    provider._offhours_metric_timer_interval_switch_count += 1


def _driver_tick_once(provider, source: str = "qt_timer") -> bool:
    """Run one completion tick without requiring a realtime market event."""

    _ensure_fields(provider)
    now_mono = time.monotonic()
    provider._offhours_metric_timer_tick_count += 1
    provider._offhours_metric_timer_last_tick_monotonic = now_mono
    provider._offhours_metric_timer_last_tick_at = datetime.now().isoformat(
        timespec="seconds"
    )
    provider._offhours_metric_timer_last_source = source

    drain = getattr(provider, "_stockboard_offhours_strength_drain", None)
    if drain is None:
        return False
    if not bool(getattr(provider, "_running", False)):
        return False

    try:
        drain.tick()
        provider._offhours_metric_timer_drain_run_count += 1
        provider._offhours_metric_timer_last_drain_monotonic = now_mono
        provider._offhours_metric_timer_last_mode = str(
            getattr(drain, "mode", "") or ""
        )
        provider._offhours_metric_timer_last_error = None
        _set_timer_interval(provider, _mode_interval_ms(provider, drain))
        return True
    except Exception as error:
        provider._offhours_metric_timer_error_count += 1
        provider._offhours_metric_timer_last_error = (
            f"{type(error).__name__}: {error}"
        )
        # Never let a completion-row error stop the collector or Qt event loop.
        try:
            drain.last_error = (
                "timer driver recovered: "
                f"{provider._offhours_metric_timer_last_error}"
            )
            drain.mode = "timer_driver_error_recovered"
            drain.next_request_at = 0.0
            drain.last_refresh_at = 0.0
        except Exception:
            pass
        _set_timer_interval(
            provider,
            int(provider._offhours_metric_timer_wait_interval_ms),
        )
        return False


def _pump_without_legacy_drain(provider, original_pump: Callable[[Any], bool]):
    """Run the old provider pump while suppressing its duplicate drain.tick call."""

    drain_name = "_stockboard_offhours_strength_drain"
    drain = getattr(provider, drain_name, None)
    if drain is None:
        return original_pump(provider)

    # The legacy wrapper calls drain.tick on every 20 ms collector pump. Hide the
    # drain only for this same-thread call; the QTimer remains the sole primary driver.
    setattr(provider, drain_name, None)
    try:
        return original_pump(provider)
    finally:
        setattr(provider, drain_name, drain)


def _driver_status(provider) -> dict[str, Any]:
    _ensure_fields(provider)
    now_mono = time.monotonic()
    last_tick = float(
        getattr(provider, "_offhours_metric_timer_last_tick_monotonic", 0.0)
        or 0.0
    )
    last_drain = float(
        getattr(provider, "_offhours_metric_timer_last_drain_monotonic", 0.0)
        or 0.0
    )
    timer = getattr(provider, "_offhours_metric_timer", None)
    timer_active = bool(
        getattr(provider, "_offhours_metric_timer_active", False)
    )
    try:
        if timer is not None:
            timer_active = bool(timer.isActive())
    except Exception:
        pass
    return {
        "driver": "qt_timer_market_tick_independent_v2_optimized",
        "market_tick_independent": True,
        "legacy_pump_drain_suppressed": True,
        "timer_active": timer_active,
        "timer_interval_ms": int(
            getattr(provider, "_offhours_metric_timer_interval_ms", 250) or 250
        ),
        "timer_active_interval_ms": int(
            getattr(provider, "_offhours_metric_timer_active_interval_ms", 250)
            or 250
        ),
        "timer_wait_interval_ms": int(
            getattr(provider, "_offhours_metric_timer_wait_interval_ms", 1000)
            or 1000
        ),
        "timer_idle_interval_ms": int(
            getattr(provider, "_offhours_metric_timer_idle_interval_ms", 10000)
            or 10000
        ),
        "timer_interval_switch_count": int(
            getattr(provider, "_offhours_metric_timer_interval_switch_count", 0)
            or 0
        ),
        "timer_tick_count": int(
            getattr(provider, "_offhours_metric_timer_tick_count", 0) or 0
        ),
        "drain_run_count": int(
            getattr(provider, "_offhours_metric_timer_drain_run_count", 0) or 0
        ),
        "timer_last_mode": getattr(
            provider, "_offhours_metric_timer_last_mode", None
        ),
        "timer_last_tick_at": getattr(
            provider, "_offhours_metric_timer_last_tick_at", None
        ),
        "timer_last_tick_age_sec": (
            round(max(0.0, now_mono - last_tick), 3)
            if last_tick > 0
            else None
        ),
        "timer_last_drain_age_sec": (
            round(max(0.0, now_mono - last_drain), 3)
            if last_drain > 0
            else None
        ),
        "timer_error_count": int(
            getattr(provider, "_offhours_metric_timer_error_count", 0) or 0
        ),
        "timer_last_error": getattr(
            provider, "_offhours_metric_timer_last_error", None
        ),
        "timer_last_source": getattr(
            provider, "_offhours_metric_timer_last_source", None
        ),
        "timer_fallback_count": int(
            getattr(provider, "_offhours_metric_timer_fallback_count", 0) or 0
        ),
    }


def install(base) -> None:
    """Install an adaptive QTimer after the final provider wrappers are in place."""

    provider_class = base.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_offhours_timer_driver_installed", False):
        return

    original_start = provider_class.start_inline_qt
    original_stop = provider_class.stop
    original_pump = provider_class.pump_inline_qt_once
    original_status = provider_class.status

    active_interval_ms = max(
        100,
        min(
            2000,
            int(os.getenv("STOCKBOARD_OFFHOURS_METRIC_TIMER_MS", "250")),
        ),
    )
    wait_interval_ms = max(
        active_interval_ms,
        min(
            5000,
            int(os.getenv("STOCKBOARD_OFFHOURS_METRIC_WAIT_TIMER_MS", "1000")),
        ),
    )
    idle_interval_ms = max(
        wait_interval_ms,
        min(
            60000,
            int(os.getenv("STOCKBOARD_OFFHOURS_METRIC_IDLE_TIMER_MS", "10000")),
        ),
    )

    def start_inline_qt(provider):
        started = original_start(provider)
        _ensure_fields(provider)
        provider._offhours_metric_timer_active_interval_ms = active_interval_ms
        provider._offhours_metric_timer_wait_interval_ms = wait_interval_ms
        provider._offhours_metric_timer_idle_interval_ms = idle_interval_ms
        provider._offhours_metric_timer_interval_ms = active_interval_ms
        if not started:
            return started
        try:
            from PyQt5.QtCore import QTimer

            timer = QTimer()
            timer.setInterval(active_interval_ms)
            timer.setSingleShot(False)
            timer.timeout.connect(
                lambda p=provider: _driver_tick_once(p, "qt_timer")
            )
            timer.start()
            provider._offhours_metric_timer = timer
            provider._offhours_metric_timer_active = True
            provider._offhours_metric_timer_last_error = None
        except Exception as error:
            provider._offhours_metric_timer_active = False
            provider._offhours_metric_timer_error_count += 1
            provider._offhours_metric_timer_last_error = (
                f"timer_start:{type(error).__name__}: {error}"
            )
        return started

    def stop(provider):
        _ensure_fields(provider)
        timer = getattr(provider, "_offhours_metric_timer", None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception as error:
                provider._offhours_metric_timer_error_count += 1
                provider._offhours_metric_timer_last_error = (
                    f"timer_stop:{type(error).__name__}: {error}"
                )
        provider._offhours_metric_timer = None
        provider._offhours_metric_timer_active = False
        return original_stop(provider)

    def pump_inline_qt_once(provider):
        ok = _pump_without_legacy_drain(provider, original_pump)
        _ensure_fields(provider)
        # QTimer is the primary driver. The main loop only recovers a genuinely
        # stalled timer and never performs a duplicate normal completion tick.
        now_mono = time.monotonic()
        last_tick = float(
            getattr(provider, "_offhours_metric_timer_last_tick_monotonic", 0.0)
            or 0.0
        )
        current_interval_sec = max(
            0.1,
            float(
                getattr(provider, "_offhours_metric_timer_interval_ms", active_interval_ms)
                or active_interval_ms
            )
            / 1000.0,
        )
        stale_after = max(1.0, current_interval_sec * 1.6 + 0.5)
        if last_tick <= 0 or now_mono - last_tick > stale_after:
            provider._offhours_metric_timer_fallback_count += 1
            _driver_tick_once(provider, "pump_fallback")
        return ok

    def status(provider):
        result = original_status(provider)
        result = result if isinstance(result, dict) else {"status": result}
        driver = _driver_status(provider)
        result["offhours_metric_timer_driver"] = dict(driver)
        scheduler = result.get("strength5m_scheduler")
        if isinstance(scheduler, dict):
            scheduler.update(driver)
        return result

    provider_class.start_inline_qt = start_inline_qt
    provider_class.stop = stop
    provider_class.pump_inline_qt_once = pump_inline_qt_once
    provider_class.status = status
    provider_class._stockboard_offhours_timer_driver_installed = True
