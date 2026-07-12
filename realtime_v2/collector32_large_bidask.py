from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Kiwoom/QAx must live on the collector main thread.
from realtime_v2.qt_main_thread_openapi_patch import (
    install_collector_main,
    install_provider,
)

install_provider()

# CommConnect must receive a real Windows HWND. Some PCs do not create a native
# handle for an unshown QAxWidget automatically, which makes opstarter report
# "핸들값이 없습니다". Install the guard before the provider is started.
from realtime_v2.openapi_native_handle_patch import (
    install as install_openapi_native_handle,
)

install_openapi_native_handle()

# Install snapshot reading and the definitive preopen controller before
# collector32_large imports strength5m_scheduler.install. Replace the former
# strength/orderbook-only drain with the central after-close coordinator before
# resilience wraps its tick loop.
from realtime_v2.strength5m_snapshot_fallback_patch import (
    install as install_strength5m_snapshot_fallback,
)
from realtime_v2.strength5m_definitive_preopen_patch import (
    install as install_strength5m_definitive_preopen,
)
from realtime_v2.offhours_metric_completion_patch import (
    install as install_offhours_metric_completion,
)
from realtime_v2.after_close_recovery_hardening import install_module_hardening
from realtime_v2.after_close_recovery import prepare_collector
from realtime_v2.offhours_metric_resilience_patch import (
    install as install_offhours_metric_resilience,
)

install_strength5m_snapshot_fallback()
install_strength5m_definitive_preopen()
install_offhours_metric_completion()
install_module_hardening()
prepare_collector()
install_offhours_metric_resilience()

large = importlib.import_module("realtime_v2.collector32_large")
base = large.base

# EventSender must survive one unserializable status/event and reconnect after any
# transient sender-loop failure. Install after collector32_large finalizes its
# EventSender extensions, but before the collector main creates the sender object.
from realtime_v2.collector_sender_resilience_patch import (
    install as install_collector_sender_resilience,
)
from realtime_v2.after_close_recovery import install_collector
from realtime_v2.after_close_recovery_hardening import install_collector_hardening

install_collector_sender_resilience(base)
install_collector(base)
install_collector_hardening(base)
install_collector_main(base)

# The after-close coordinator must be driven by its own Qt timer rather than by
# incoming trade/orderbook ticks. Install this after all provider wrappers.
from realtime_v2.offhours_metric_timer_driver_patch import (
    install as install_offhours_metric_timer_driver,
)

install_offhours_metric_timer_driver(base)


def _runtime_dir() -> Path:
    try:
        return Path(base.RUNTIME_DIR)
    except Exception:
        return ROOT / "data" / "runtime" / "stockboard_v2"


def _install_orderbook_thin_fail_open() -> None:
    try:
        from realtime_v2.orderbook_thin_scheduler import (
            install as install_orderbook_thin_scheduler,
        )

        install_orderbook_thin_scheduler(base)
    except Exception as error:
        # The collector must remain usable even if the optional thin scheduler fails.
        try:
            runtime = _runtime_dir()
            runtime.mkdir(parents=True, exist_ok=True)
            (runtime / "bidask_collector_patch_error.txt").write_text(
                f"{type(error).__name__}: {error}\n\n{traceback.format_exc()}",
                encoding="utf-8",
            )
        except Exception:
            pass


_install_orderbook_thin_fail_open()

if __name__ == "__main__":
    raise SystemExit(base.main())
