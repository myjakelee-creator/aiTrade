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

# Install snapshot reading and the definitive preopen controller before
# collector32_large imports strength5m_scheduler.install.  Then replace its
# strength-only drain with the unified off-hours metric completion controller.
from realtime_v2.strength5m_snapshot_fallback_patch import (
    install as install_strength5m_snapshot_fallback,
)
from realtime_v2.strength5m_definitive_preopen_patch import (
    install as install_strength5m_definitive_preopen,
)
from realtime_v2.offhours_metric_completion_patch import (
    install as install_offhours_metric_completion,
)

install_strength5m_snapshot_fallback()
install_strength5m_definitive_preopen()
install_offhours_metric_completion()

large = importlib.import_module("realtime_v2.collector32_large")
base = large.base
install_collector_main(base)


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
