from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any


def _ensure_fields(provider) -> None:
    defaults = {
        "_offhours_metric_timer": None,
        "_offhours_metric_timer_active": False,
        "_offhours_metric_timer_interval_ms": 250,
        "_offhours_metric_timer_tick_count": 0,
        "_offhours_metric_timer_last_tick_monotonic": 0.0,
        "_offhours_metric_timer_last_tick_at": None,
        "_offhours_metric_timer_error_count": 0,
        "_offhours_metric_timer_last_error": None,
        "_offhours_metric_timer_last_source": None,
        "_offhours_metric_timer_fallback_count": 0,
    }
    for name, value in defaults.items():
        if not hasattr(provider, name):
            setattr(provider, name, value)


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
        provider._offhours_metric_timer_last_error = None
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
        return False


def _driver_status(provider) -> dict[str, Any]:
    _ensure_fields(provider)
    now_mono = time.monotonic()
    last_tick = float(
        getattr(provider, "_offhours_metric_timer_last_tick_monotonic", 0.0)
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
        "driver": "qt_timer_market_tick_independent_v1",
        "market_tick_independent": True,
        "timer_active": timer_active,
        "timer_interval_ms": int(
            getattr(provider, "_offhours_metric_timer_interval_ms", 250) or 250
        ),
        "timer_tick_count": int(
            getattr(provider, "_offhours_metric_timer_tick_count", 0) or 0
        ),
        "timer_last_tick_at": getattr(
            provider, "_offhours_metric_timer_last_tick_at", None
        ),
        "timer_last_tick_age_sec": (
            round(max(0.0, now_mono - last_tick), 3)
            if last_tick > 0
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
    """Install a QTimer driver after the final provider wrappers are in place."""

    provider_class = base.KiwoomOpenApiRealtimeProvider
    if getattr(provider_class, "_stockboard_offhours_timer_driver_installed", False):
        return

    original_start = provider_class.start_inline_qt
    original_stop = provider_class.stop
    original_pump = provider_class.pump_inline_qt_once
    original_status = provider_class.status

    interval_ms = max(
        100,
        min(
            2000,
            int(os.getenv("STOCKBOARD_OFFHOURS_METRIC_TIMER_MS", "250")),
        ),
    )

    def start_inline_qt(provider):
        started = original_start(provider)
        _ensure_fields(provider)
        provider._offhours_metric_timer_interval_ms = interval_ms
        if not started:
            return started
        try:
            from PyQt5.QtCore import QTimer

            timer = QTimer()
            timer.setInterval(interval_ms)
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
        ok = original_pump(provider)
        _ensure_fields(provider)
        # The normal QTimer is the primary no-market-tick driver.  Keep a main-loop
        # fallback so a missed timer callback can never require reconnecting.
        now_mono = time.monotonic()
        last_tick = float(
            getattr(provider, "_offhours_metric_timer_last_tick_monotonic", 0.0)
            or 0.0
        )
        stale_after = max(1.0, interval_ms / 1000.0 * 4.0)
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
