from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Restore the last verified production collector path. The QAx control and its
# Qt event pump are owned by KiwoomOpenApiRealtimeProvider.start(), as they were
# before the main-thread/QTimer/off-hours patch stack was introduced.
large = importlib.import_module("realtime_v2.collector32_large")
base = large.base


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
        # The price collector must remain usable if optional thin bid/ask support
        # cannot be installed.
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
