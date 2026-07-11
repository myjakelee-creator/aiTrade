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
        # Physical Qt heartbeat stays fixed. Heavy drain work is gated separately.
        "_offhours_metric_timer_interval_ms": 250,
        "_offhours_metric_timer_active_interval_ms": 250,
        "_offhours_metric_timer_wait_interval_ms": 1000,
        "_offhours_metric_timer_idle_interval_ms": 10000,
        "_offhours_metric_timer_effective_drain_interval_ms": 250,
        "_offhours_metric_timer_next_drain_monotonic": 0.0,
        "_offhours_metric_timer_tick_count": 0,
        "_offhours_metric_timer_last_tick_monotonic": 0.0,
        "_offhours_metric_timer_last_tick_at": None,
        "_offhours_metric_timer_error_count": 0,
        "_offhours_metric_timer_last_error": None,
        "_offhours_metric_timer_last_source": None,
        "_offhours_metric_timer_fallback_count": 0,
        "_offhours_metric_timer_drain_run_count": 0,
        "_offhours_metric_timer_drain_skip_count": 0,
        "_offhours_metric_timer_last_drain_monotonic": 0.0,
        "_offhours_metric_timer_last_mode": None,
        "_offhours_metric_timer_interval_switch_count": 0,
        "_offhours_metric_legacy_pump_unwrapped": False,
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


def _set_effective_interval(provider, interval_ms: int) -> None:
    _ensure_fields(provider)
    interval_ms = max(100, int(interval_ms))
    current = int(
        getattr(
            provider,
            "_offhours_metric_timer_effective_drain_interval_ms",
            interval_ms,
        )
        or interval_ms
    )
    if current != interval_ms:
        provider._offhours_metric_timer_interval_switch_count += 1
    provider._offhours_metric_timer_effective_drain_interval_ms = interval_ms


def _driver_tick_once(
    provider,
    source: str = "qt_timer",
    *,
    force: bool = False,
) -> bool:
    """Run one market-tick-independent heartbeat and due drain cycle.

    The QTimer itself always fires at the small fixed heartbeat. Expensive snapshot
    and queue work runs only when the mode-specific cadence is due.
    """

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

    desired_before = _mode_interval_ms(provider, drain)
    _set_effective_interval(provider, desired_before)
    next_due = float(
        getattr(provider, "_offhours_metric_timer_next_drain_monotonic", 0.0)
        or 0.0
    )
    if not force and next_due > now_mono:
        provider._offhours_metric_timer_drain_skip_count += 1
        return True

    try:
        drain.tick()
        provider._offhours_metric_timer_drain_run_count += 1
        provider._offhours_metric_timer_last_drain_monotonic = now_mono
        provider._offhours_metric_timer_last_mode = str(
            getattr(drain, "mode", "") or ""
        )
        provider._offhours_metric_timer_last_error = None
        desired_after = _mode_interval_ms(provider, drain)
        _set_effective_interval(provider, desired_after)
        provider._offhours_metric_timer_next_drain_monotonic = (
            now_mono + desired_after / 1000.0
        )
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
        wait_ms = int(provider._offhours_metric_timer_wait_interval_ms)
        _set_effective_interval(provider, wait_ms)
        provider._offhours_metric_timer_next_drain_monotonic = (
            now_mono + wait_ms / 1000.0
        )
        return False


def _unwrap_legacy_drain_pump(
    pump: Callable[[Any], bool],
) -> tuple[Callable[[Any], bool], bool]:
    """Return the Qt pump below the old wrapper that called drain.tick every 20 ms.

    The old wrapper closes over a callable named ``original_pump``. Calling that
    underlying function preserves QApplication.processEvents/QTimer delivery while
    removing only the duplicate off-hours completion call.
    """

    freevars = tuple(getattr(getattr(pump, "__code__", None), "co_freevars", ()))
    closure = tuple(getattr(pump, "__closure__", ()) or ())
    if not freevars or len(freevars) != len(closure):
        return pump, False

    closed = {}
    for name, cell in zip(freevars, closure):
        try:
            closed[name] = cell.cell_contents
        except ValueError:
            continue
    candidate = closed.get("original_pump")
    if callable(candidate) and candidate is not pump:
        return candidate, True
    return pump, False


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
    next_drain = float(
        getattr(provider, "_offhours_metric_timer_next_drain_monotonic", 0.0)
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
        "driver": "qt_timer_market_tick_independent_v3_fixed_heartbeat",
        "market_tick_independent": True,
        "legacy_pump_drain_suppressed": bool(
            getattr(provider, "_offhours_metric_legacy_pump_unwrapped", False)
        ),
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
        "effective_drain_interval_ms": int(
            getattr(
                provider,
                "_offhours_metric_timer_effective_drain_interval_ms",
                250,
            )
            or 250
        ),
        "next_drain_in_sec": round(max(0.0, next_drain - now_mono), 3),
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
        "drain_skip_count": int(
            getattr(provider, "_offhours_metric_timer_drain_skip_count", 0) or 0
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
    """Install a fixed-heartbeat QTimer after final provider wrappers are in place."""

    provider_class = base.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_offhours_timer_driver_installed", False):
        return

    original_start = provider_class.start_inline_qt
    original_stop = provider_class.stop
    wrapped_pump = provider_class.pump_inline_qt_once
    original_pump, legacy_pump_unwrapped = _unwrap_legacy_drain_pump(wrapped_pump)
    original_status = provider_class.status

    heartbeat_interval_ms = max(
        100,
        min(
            1000,
            int(os.getenv("STOCKBOARD_OFFHOURS_METRIC_TIMER_MS", "250")),
        ),
    )
    wait_interval_ms = max(
        heartbeat_interval_ms,
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
        provider._offhours_metric_timer_active_interval_ms = heartbeat_interval_ms
        provider._offhours_metric_timer_wait_interval_ms = wait_interval_ms
        provider._offhours_metric_timer_idle_interval_ms = idle_interval_ms
        provider._offhours_metric_timer_interval_ms = heartbeat_interval_ms
        provider._offhours_metric_timer_effective_drain_interval_ms = (
            heartbeat_interval_ms
        )
        provider._offhours_metric_timer_next_drain_monotonic = 0.0
        provider._offhours_metric_legacy_pump_unwrapped = legacy_pump_unwrapped
        if not started:
            return started
        try:
            from PyQt5.QtCore import QTimer

            timer = QTimer()
            timer.setInterval(heartbeat_interval_ms)
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
        # Call the underlying Qt pump, not the legacy wrapper that also called
        # drain.tick. QTimer callbacks remain deliverable inside processEvents().
        ok = original_pump(provider)
        _ensure_fields(provider)
        provider._offhours_metric_legacy_pump_unwrapped = legacy_pump_unwrapped
        # The fixed QTimer is primary. Main loop only recovers a truly stalled
        # heartbeat; it never performs a duplicate normal completion tick.
        now_mono = time.monotonic()
        last_tick = float(
            getattr(provider, "_offhours_metric_timer_last_tick_monotonic", 0.0)
            or 0.0
        )
        heartbeat_sec = max(0.1, heartbeat_interval_ms / 1000.0)
        stale_after = max(1.5, heartbeat_sec * 5.0)
        if last_tick <= 0 or now_mono - last_tick > stale_after:
            provider._offhours_metric_timer_fallback_count += 1
            _driver_tick_once(provider, "pump_fallback", force=True)
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
